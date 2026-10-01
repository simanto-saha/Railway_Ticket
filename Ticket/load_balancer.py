"""Async HTTP and WebSocket load balancer for the local Daphne workers."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlsplit

from aiohttp import (
    ClientError,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    TCPConnector,
    WSMsgType,
    WSServerHandshakeError,
    web,
)
from multidict import CIMultiDict
from yarl import URL


LOGGER = logging.getLogger("load_balancer")
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
WEBSOCKET_HEADERS = {
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
}
MAX_WEBSOCKET_MESSAGE_SIZE = 16 * 1024 * 1024


@dataclass
class Backend:
    origin: str
    host: str
    port: int
    ssl: bool
    healthy: bool = False


def configured_backends() -> list[Backend]:
    values = os.getenv(
        "BALANCER_BACKENDS",
        "http://127.0.0.1:8000,http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003",
    )
    backends = []
    for value in values.split(","):
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Invalid backend URL: {value!r}")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError(f"Backend URLs must contain only scheme, host, and port: {value!r}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        backends.append(
            Backend(
                origin=f"{parsed.scheme}://{parsed.netloc}",
                host=parsed.hostname,
                port=port,
                ssl=parsed.scheme == "https",
            )
        )
    if not backends:
        raise ValueError("BALANCER_BACKENDS must include at least one backend")
    return backends


def forwarded_headers(raw_headers: tuple[tuple[bytes, bytes], ...], request: web.Request) -> list[tuple[str, str]]:
    connection_tokens = set()
    for name, value in raw_headers:
        if name.lower() == b"connection":
            connection_tokens.update(token.strip().lower() for token in value.decode("latin-1").split(","))

    excluded = HOP_BY_HOP_HEADERS | connection_tokens
    headers = [
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in raw_headers
        if name.decode("latin-1").lower() not in excluded
        and name.decode("latin-1").lower() not in {"expect", *WEBSOCKET_HEADERS}
    ]

    peer = request.transport.get_extra_info("peername") if request.transport else None
    client_ip = peer[0] if peer else ""
    headers.extend(
        [
            ("X-Forwarded-For", client_ip),
            ("X-Forwarded-Proto", request.scheme),
            ("X-Forwarded-Host", request.headers.get("Host", "")),
        ]
    )
    return headers


def response_headers(raw_headers: tuple[tuple[bytes, bytes], ...]) -> CIMultiDict[str]:
    connection_tokens = set()
    for name, value in raw_headers:
        if name.lower() == b"connection":
            connection_tokens.update(token.strip().lower() for token in value.decode("latin-1").split(","))
    excluded = HOP_BY_HOP_HEADERS | connection_tokens
    headers: CIMultiDict[str] = CIMultiDict()
    for name, value in raw_headers:
        decoded_name = name.decode("latin-1")
        if decoded_name.lower() not in excluded:
            headers.add(decoded_name, value.decode("latin-1"))
    return headers


class LoadBalancer:
    def __init__(self, backends: list[Backend]) -> None:
        self.backends = backends
        self._next_index = 0
        self._selection_lock = asyncio.Lock()
        self._health_lock = asyncio.Lock()
        self.request_count = 0
        self.websocket_count = 0
        self.active_websockets = 0
        self.backend_failures = 0
        self.client_disconnects = 0
        self.status_counts: Counter[str] = Counter()

    async def choose_backend(self) -> Backend | None:
        async with self._selection_lock:
            for offset in range(len(self.backends)):
                index = (self._next_index + offset) % len(self.backends)
                backend = self.backends[index]
                if backend.healthy:
                    self._next_index = (index + 1) % len(self.backends)
                    return backend
        return None

    async def set_health(self, backend: Backend, healthy: bool) -> None:
        async with self._health_lock:
            if backend.healthy and not healthy:
                self.backend_failures += 1
            backend.healthy = healthy

    async def check_backend(self, backend: Backend) -> None:
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(backend.host, backend.port, ssl=backend.ssl),
                timeout=float(os.getenv("BALANCER_CONNECT_TIMEOUT", "2")),
            )
        except (OSError, asyncio.TimeoutError, ValueError):
            await self.set_health(backend, False)
        else:
            await self.set_health(backend, True)
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    async def check_backends(self) -> None:
        await asyncio.gather(*(self.check_backend(backend) for backend in self.backends))

    def stats(self) -> dict[str, object]:
        return {
            "requests": self.request_count,
            "websocket_connections": self.websocket_count,
            "active_websockets": self.active_websockets,
            "backend_failures": self.backend_failures,
            "client_disconnects": self.client_disconnects,
            "statuses": dict(self.status_counts),
            "backends": [
                {"url": backend.origin, "healthy": backend.healthy}
                for backend in self.backends
            ],
        }


async def proxy_lifecycle(app: web.Application) -> AsyncIterator[None]:
    balancer: LoadBalancer = app["balancer"]
    timeout = ClientTimeout(
        total=float(os.getenv("BALANCER_READ_TIMEOUT", "60")),
        sock_connect=float(os.getenv("BALANCER_CONNECT_TIMEOUT", "2")),
        sock_read=float(os.getenv("BALANCER_READ_TIMEOUT", "60")),
    )
    connector = TCPConnector(
        limit=int(os.getenv("BALANCER_MAX_CONNECTIONS", "4096")),
        ttl_dns_cache=300,
        keepalive_timeout=30,
        enable_cleanup_closed=True,
    )
    app["client"] = ClientSession(
        connector=connector,
        timeout=timeout,
        auto_decompress=False,
        skip_auto_headers={"Accept-Encoding"},
    )
    await balancer.check_backends()

    async def health_loop() -> None:
        interval = float(os.getenv("BALANCER_HEALTH_INTERVAL", "2"))
        while True:
            await asyncio.sleep(interval)
            await balancer.check_backends()

    health_task = asyncio.create_task(health_loop(), name="backend-health-check")
    try:
        yield
    finally:
        health_task.cancel()
        with suppress(asyncio.CancelledError):
            await health_task
        await app["client"].close()


async def health(request: web.Request) -> web.Response:
    balancer: LoadBalancer = request.app["balancer"]
    has_backend = any(backend.healthy for backend in balancer.backends)
    return web.json_response(
        {"status": "ok" if has_backend else "unavailable", "backends": balancer.stats()["backends"]},
        status=200 if has_backend else 503,
    )


async def stats(request: web.Request) -> web.Response:
    return web.json_response(request.app["balancer"].stats())


async def relay_websocket(
    client_ws: web.WebSocketResponse,
    backend_ws: ClientWebSocketResponse,
) -> None:
    async def pump(
        source: web.WebSocketResponse | ClientWebSocketResponse,
        target: web.WebSocketResponse | ClientWebSocketResponse,
    ) -> None:
        async for message in source:
            if message.type == WSMsgType.TEXT:
                await target.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await target.send_bytes(message.data)
            elif message.type == WSMsgType.PING:
                await target.ping(message.data)
            elif message.type == WSMsgType.PONG:
                await target.pong(message.data)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                return

    client_to_backend = asyncio.create_task(pump(client_ws, backend_ws))
    backend_to_client = asyncio.create_task(pump(backend_ws, client_ws))
    try:
        done, pending = await asyncio.wait(
            {client_to_backend, backend_to_client},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            # Connection reset etc. during relay is normal; don't bubble up as a 500.
            with suppress(Exception):
                task.result()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for task in (client_to_backend, backend_to_client):
            if not task.done():
                task.cancel()
        await asyncio.gather(client_to_backend, backend_to_client, return_exceptions=True)


async def proxy_websocket(request: web.Request, backend: Backend) -> web.StreamResponse:
    balancer: LoadBalancer = request.app["balancer"]
    client: ClientSession = request.app["client"]
    protocols = tuple(
        protocol.strip()
        for protocol in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if protocol.strip()
    )
    backend_url = URL(backend.origin + request.raw_path, encoded=True).with_scheme(
        "wss" if backend.ssl else "ws"
    )
    started = time.perf_counter()
    try:
        upstream = await client.ws_connect(
            backend_url,
            headers=forwarded_headers(request.raw_headers, request),
            protocols=protocols,
            heartbeat=30,
            max_msg_size=MAX_WEBSOCKET_MESSAGE_SIZE,
        )
    except WSServerHandshakeError as exc:
        balancer.status_counts[str(exc.status)] += 1
        return web.Response(status=exc.status, text=exc.message)
    except (ClientError, asyncio.TimeoutError, OSError) as exc:
        await balancer.set_health(backend, False)
        balancer.status_counts["502"] += 1
        LOGGER.warning("path=%s backend=%s status=502 elapsed_ms=%.1f error=%s", request.path_qs, backend.origin, (time.perf_counter() - started) * 1000, exc)
        return web.Response(status=502, text="Bad Gateway")

    client_ws = web.WebSocketResponse(
        protocols=upstream.protocol and (upstream.protocol,) or (),
        heartbeat=30,
        max_msg_size=MAX_WEBSOCKET_MESSAGE_SIZE,
    )
    await client_ws.prepare(request)
    balancer.active_websockets += 1
    balancer.websocket_count += 1
    try:
        await relay_websocket(client_ws, upstream)
    finally:
        balancer.active_websockets -= 1
        await upstream.close()
        await client_ws.close()

    balancer.status_counts["101"] += 1
    LOGGER.info("path=%s backend=%s status=101 elapsed_ms=%.1f", request.path_qs, backend.origin, (time.perf_counter() - started) * 1000)
    return client_ws


async def proxy(request: web.Request) -> web.StreamResponse:
    balancer: LoadBalancer = request.app["balancer"]
    balancer.request_count += 1
    backend = await balancer.choose_backend()
    if backend is None:
        balancer.status_counts["502"] += 1
        return web.Response(status=502, text="No healthy backend available")

    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await proxy_websocket(request, backend)

    client: ClientSession = request.app["client"]
    backend_url = URL(backend.origin + request.raw_path, encoded=True)
    started = time.perf_counter()
    status = 502
    downstream: web.StreamResponse | None = None
    client_gone = False
    try:
        async with client.request(
            request.method,
            backend_url,
            headers=forwarded_headers(request.raw_headers, request),
            # Only stream a body when the client actually sent one; otherwise
            # aiohttp adds a bogus "Transfer-Encoding: chunked" to GET requests.
            data=request.content if request.body_exists else None,
            allow_redirects=False,
        ) as upstream:
            status = upstream.status
            downstream = web.StreamResponse(
                status=upstream.status,
                reason=upstream.reason,
                headers=response_headers(upstream.raw_headers),
            )
            # ConnectionResetError here means the *client* went away,
            # which is not the backend's fault, so don't mark it unhealthy.
            try:
                await downstream.prepare(request)
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await downstream.write(chunk)
                await downstream.write_eof()
            except ConnectionResetError:
                client_gone = True
    except asyncio.TimeoutError as exc:
        # A timeout means the backend is slow, not dead. Marking it unhealthy here
        # makes overload cascade into 502s; the health loop still catches dead backends.
        status = 504
        if downstream is not None and downstream.prepared:
            if request.transport:
                request.transport.close()
            raise
        response = web.Response(status=504, text="Gateway Timeout")
        balancer.status_counts[str(status)] += 1
        LOGGER.warning("path=%s backend=%s status=%d elapsed_ms=%.1f error=%s", request.path_qs, backend.origin, status, (time.perf_counter() - started) * 1000, exc)
        return response
    except (ClientError, OSError) as exc:
        await balancer.set_health(backend, False)
        status = 502
        if downstream is not None and downstream.prepared:
            if request.transport:
                request.transport.close()
            raise
        response = web.Response(status=502, text="Bad Gateway")
        balancer.status_counts[str(status)] += 1
        LOGGER.warning("path=%s backend=%s status=%d elapsed_ms=%.1f error=%s", request.path_qs, backend.origin, status, (time.perf_counter() - started) * 1000, exc)
        return response

    if client_gone:
        balancer.client_disconnects += 1
        LOGGER.info("path=%s backend=%s status=%d client_disconnected=1 elapsed_ms=%.1f", request.path_qs, backend.origin, status, (time.perf_counter() - started) * 1000)
        return downstream

    balancer.status_counts[str(status)] += 1
    LOGGER.info("path=%s backend=%s status=%d elapsed_ms=%.1f", request.path_qs, backend.origin, status, (time.perf_counter() - started) * 1000)
    return downstream


def create_app() -> web.Application:
    logging.basicConfig(
        level=os.getenv("BALANCER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = web.Application(client_max_size=0)
    app["balancer"] = LoadBalancer(configured_backends())
    app.cleanup_ctx.append(proxy_lifecycle)
    app.router.add_get("/health", health)
    app.router.add_get("/stats", stats)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


if __name__ == "__main__":
    web.run_app(
        create_app(),
        host=os.getenv("BALANCER_HOST", "127.0.0.1"),
        port=int(os.getenv("BALANCER_PORT", "9000")),
        access_log=None,
        shutdown_timeout=30,
    )