# RailwaySheba

RailwaySheba is a modern railway ticket booking platform designed to provide a fast, reliable, and real-time ticket booking experience.

The platform is built with Django, ASGI, WebSockets, Redis, PostgreSQL, and PgBouncer, with a focus on real-time communication, concurrent booking, and scalable database connection management.

## Features

* Railway ticket booking
* Real-time seat availability
* Real-time updates using WebSockets
* User authentication and authorization
* User account management
* Ticket booking and management
* Train and route management
* Travel date and schedule management
* Real-time booking status updates
* PostgreSQL database
* Redis-based real-time communication
* PgBouncer-based database connection pooling
* ASGI-based asynchronous architecture

## Technology Stack

| Technology              | Purpose                               |
| ----------------------- | ------------------------------------- |
| Python                  | Backend programming                   |
| Django                  | Web framework                         |
| Django Channels         | WebSocket support                     |
| ASGI                    | Asynchronous server interface         |
| Daphne                  | ASGI application server               |
| WebSocket               | Real-time communication               |
| Redis                   | Channel layer and real-time messaging |
| PostgreSQL              | Primary database                      |
| PgBouncer               | Database connection pooling           |
| HTML / CSS / JavaScript | Frontend                              |

## System Architecture

```text
                         Client
                           |
                    HTTP / WebSocket
                           |
                           v
                    Nginx / Proxy
                           |
                           v
                         Daphne
                           |
                 +---------+---------+
                 |                   |
                 v                   v
              Django               Redis
                 |                   |
                 |            WebSocket Channel
                 |                Layer
                 |
                 v
             PgBouncer
                 |
                 v
             PostgreSQL
```

## Real-Time Communication

RailwaySheba uses WebSockets to provide real-time communication between the server and connected clients.

Typical flow:

```text
User opens seat selection
          |
          v
WebSocket connection established
          |
          v
Server monitors booking events
          |
          v
Seat status changes
          |
          v
Redis Channel Layer
          |
          v
Connected clients receive updates
```

This allows seat availability and booking-related information to be updated without requiring continuous page refreshes.

## Redis

Redis is used as the channel layer for WebSocket communication.

It allows multiple Django/ASGI processes to communicate with each other and broadcast real-time events to connected clients.

```text
Client A
   |
   v
Daphne / ASGI
   |
   v
 Redis
   |
   v
Daphne / ASGI
   |
   v
Client B
```

## PostgreSQL and PgBouncer

PostgreSQL is used as the primary relational database.

PgBouncer sits between the application and PostgreSQL and manages database connections through connection pooling.

For the project's 10,000-client PgBouncer example, backend-pool sizing guidance, and host file-descriptor requirements, see [Ticket/PGBOUNCER.md](Ticket/PGBOUNCER.md) and [Ticket/pgbouncer.ini.example](Ticket/pgbouncer.ini.example). The client limit is not a guarantee of 10,000 simultaneous database queries; validate capacity gradually against the deployed database and host.

```text
Django
   |
   v
PgBouncer
   |
   v
PostgreSQL
```

This architecture helps reduce the overhead of creating large numbers of direct PostgreSQL connections, particularly when the application is handling many concurrent requests.

## ASGI Architecture

RailwaySheba uses ASGI instead of a traditional WSGI-only architecture so that the application can support asynchronous communication and WebSockets.

```text
                    Client
                      |
             +--------+--------+
             |                 |
            HTTP          WebSocket
             |                 |
             v                 v
          Django         Django Channels
                               |
                               v
                             Redis
```

## Project Structure

```text
RailwaySheba/
|
├── manage.py
├── requirements.txt
|
├── RailwaySheba/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── routing.py
|
├── accounts/
├── tickets/
├── trains/
├── bookings/
|
├── templates/
├── static/
|
└── README.md
```

The project structure may change as development continues.

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/RailwaySheba.git
cd RailwaySheba
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Linux / macOS:

```bash
source venv/bin/activate
```

Windows:

```bash
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file:

```env
DEBUG=True

SECRET_KEY=your-secret-key

DB_NAME=railwaysheba
DB_USER=postgres
DB_PASSWORD=your-password
DB_HOST=127.0.0.1
DB_PORT=5432

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

Do not commit the `.env` file to the repository.

### 5. Run Database Migrations

```bash
python manage.py migrate
```

### 6. Create a Superuser

```bash
python manage.py createsuperuser
```

### 7. Start Redis

Make sure Redis is running:

```bash
redis-server
```

### 8. Start the ASGI Server

Using Daphne:

```bash
daphne RailwaySheba.asgi:application
```

For development, Django's development server can also be used:

```bash
python manage.py runserver
```

## Production Architecture

A production deployment can use the following architecture:

```text
                         Internet
                            |
                            v
                          Nginx
                            |
                   +--------+--------+
                   |                 |
                  HTTP          WebSocket
                   |                 |
                   +--------+--------+
                            |
                            v
                         Daphne
                            |
                 +----------+----------+
                 |                     |
                 v                     v
              Django                 Redis
                 |
                 v
             PgBouncer
                 |
                 v
             PostgreSQL
```

## Security

The project follows Django security practices such as:

* Environment-based secret configuration
* CSRF protection
* Authentication and authorization
* Secure password handling
* PostgreSQL-based data storage
* Production-ready ASGI deployment

Before production deployment, configure appropriate values for:

```env
DEBUG=False
ALLOWED_HOSTS=your-domain.com
CSRF_TRUSTED_ORIGINS=https://your-domain.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

## Development

Start the development server:

```bash
python manage.py runserver
```

For ASGI and WebSocket development:

```bash
daphne RailwaySheba.asgi:application
```

Make sure Redis and PostgreSQL are running before testing WebSocket and database-dependent features.

## Docker Deployment

The Docker stack is in `Ticket/`. It runs Nginx, Daphne/Django, PgBouncer, PostgreSQL, and Redis. Django remains the existing ASGI application (`Ticket.asgi:application`); no app or database logic is replaced.

```text
Internet -> Nginx:80 (optional TLS:443) -> Daphne/Django:8000
                                      |-> PgBouncer:6432 -> PostgreSQL:5432
                                      |-> Redis:6379 (Channels and cache)
```

Only Nginx publishes a host port. Nginx and Daphne share an isolated frontend network; Django, PgBouncer, and Redis share an isolated application network; PostgreSQL and PgBouncer share an isolated database network. Nginx cannot connect directly to Redis or PostgreSQL. Django also has outbound-only network access for SSLCommerz and SMTP. It addresses its dependencies by service name (`pgbouncer`, `postgres`, and `redis`), never Docker loopback. Static and uploaded media use shared named volumes between Django and Nginx; PostgreSQL and Redis data also persist in named volumes.

### Configuration and Start

Run these commands from `Ticket/`. If no `.env` exists, create it from the example. If you already have `.env`, do not overwrite it; add any missing settings from `.env.example` instead:

```bash
test -f .env || cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Put the generated value in `SECRET_KEY` in `.env`. Set strong unique values for `DB_PASSWORD`, email/payment credentials, and configure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for the deployment domain. Protect `.env` with restrictive file permissions and do not commit it. The existing local `.env` should be preserved; add `SECRET_KEY` to it before running Django with the environment-based settings.

For local Docker testing, use `DEBUG=True` and set `NGINX_BIND_ADDRESS=127.0.0.1` in `.env`. For production, set `DEBUG=False`, provide the public hostname in `ALLOWED_HOSTS`, set HTTPS origins in `CSRF_TRUSTED_ORIGINS`, and enable `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, and `CSRF_COOKIE_SECURE` after TLS is active. The same `docker-compose.yml` is used in both cases: service topology does not change, and a second Compose file would duplicate security-sensitive configuration. Production deployments should use a managed secret store where available; `.env` is for a single-host Compose deployment.

```bash
docker compose up -d --build
```

The web container runs `python manage.py collectstatic --noinput` before starting Daphne. Database migrations remain an explicit release step so multiple web instances cannot race to apply them. PgBouncer listens on `6432`, PostgreSQL listens internally on `5432`, and Django uses transaction pooling with `CONN_MAX_AGE=0` and `DISABLE_SERVER_SIDE_CURSORS=True`. PgBouncer allows up to 1,000 client connections, targets 25 server connections per database/user pool, allows a reserve pool of 10, and caps the database and user at 50 backend connections. Its `scram-sha-256` auth file is generated in the container at startup from `DB_USER` and `DB_PASSWORD`; the tracked `userlist.txt` contains no credentials.

The pre-existing root-level `Ticket/pgbouncer.ini.example` and `Ticket/PGBOUNCER.md` describe a host-installed PgBouncer and are not mounted or used by this Compose stack. Docker uses the separate `Ticket/pgbouncer/pgbouncer.ini` configuration.

Nginx serves `/static/` and `/media/` directly from shared volumes, supports large uploads and long-lived WebSockets, and forwards host/protocol/client-IP headers to Daphne. WhiteNoise remains configured as a fallback. Daphne accepts ASGI HTTP and WebSocket protocols; Channels uses Redis so group events can reach connections served by different Daphne processes.

### Operations

Run from `Ticket/`:

```bash
# Build and start
docker compose up -d --build

# Stop services but keep all named volumes/data
docker compose down

# Restart
docker compose restart

# Follow all logs, or one service
docker compose logs -f
docker compose logs -f web

# Apply migrations and create an administrator
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser

# Collect static files manually when needed
docker compose exec web python manage.py collectstatic --noinput

# Django shell
docker compose exec web python manage.py shell

# PostgreSQL shell
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'

# Redis check (PONG means healthy)
docker compose exec redis redis-cli -n 1 ping

# PgBouncer listener check
docker compose exec pgbouncer sh -c 'pg_isready -h 127.0.0.1 -p 6432 -U "$DB_USER" -d "$DB_NAME"'

# Rebuild and replace services after code/config changes
docker compose up -d --build

# Back up PostgreSQL to the host
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > railway-ticket.sql

# Restore a backup into the configured database
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < railway-ticket.sql
```

`docker compose down` removes containers and networks but preserves PostgreSQL, Redis, static, and media volumes. Do not use `docker compose down -v` unless intentionally deleting all persistent data. Backups should be stored outside the host and restore should be tested against a non-production copy. To inspect health use `docker compose ps` and `docker compose logs pgbouncer postgres redis`.

### HTTPS

The default Nginx listener is HTTP on port 80. To enable TLS, obtain certificates, copy `Ticket/nginx/https-server.conf.example` to `Ticket/nginx/https-server.conf`, set its `server_name`, mount `./nginx/certs:/etc/nginx/certs:ro` and `./nginx/https-server.conf:/etc/nginx/conf.d/https-server.conf:ro` in the Nginx service, and add `"443:443"` to its published ports. Set the trusted HTTPS origin and secure-cookie/redirect environment variables. Once verified, make the port-80 server redirect to HTTPS. Terminate TLS only at a trusted proxy; Nginx overwrites the forwarded headers used by Django.

### Capacity and Notes

Compose resource limits are conservative starting points, not capacity guarantees; tune them to the host and observed workload. PgBouncer pool values leave room below PostgreSQL's default connection limit, but should be checked against the deployed database and other clients. The app network is private, and only the web container receives outbound access required by payment and email integrations. For public production traffic, configure host firewall rules, TLS, secure secrets, monitoring, and tested database backups before opening Nginx to the internet.

## Local Async Load Balancer

The lightweight asyncio proxy in `Ticket/load_balancer.py` distributes HTTP and WebSocket connections across the two local Daphne processes. It checks backend TCP health every two seconds by default, uses round-robin selection among healthy backends, and re-adds a backend after it responds to a later health check. HTTP requests are streamed and the aiohttp client reuses upstream keep-alive connections.

Install the proxy dependency from the Django project directory:

```bash
cd Ticket
pip install -r requirements.txt
```

Run each process in a separate terminal from `Ticket/`:

```bash
daphne -b 127.0.0.1 -p 8000 Ticket.asgi:application
daphne -b 127.0.0.1 -p 8001 Ticket.asgi:application
python load_balancer.py
```

Browse to `http://127.0.0.1:9000`. Check proxy/backend availability at `/health` and request/backend counters at `/stats`. To load test the balancer, run `locust -f locustfile.py` from `Ticket/`; the included Locust user targets port `9000`.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BALANCER_HOST` | `127.0.0.1` | Listener address |
| `BALANCER_PORT` | `9000` | Listener port |
| `BALANCER_BACKENDS` | `http://127.0.0.1:8000,http://127.0.0.1:8001` | Comma-separated backend URLs |
| `BALANCER_HEALTH_INTERVAL` | `2` | Seconds between health checks |
| `BALANCER_CONNECT_TIMEOUT` | `2` | Backend connection timeout, seconds |
| `BALANCER_READ_TIMEOUT` | `60` | Backend request/read timeout, seconds |
| `BALANCER_MAX_CONNECTIONS` | `4096` | Maximum pooled upstream connections |
| `BALANCER_LOG_LEVEL` | `INFO` | Proxy log level |

This listener is intentionally bound to loopback and is a local development/load-test proxy, not a hardened public edge proxy. For production internet traffic, use a maintained reverse proxy or load balancer with TLS termination, access controls, and deployment-grade monitoring.

## Project Goals

RailwaySheba is designed around the following goals:

* Provide a reliable railway ticket booking experience
* Provide real-time seat availability
* Handle concurrent users efficiently
* Reduce unnecessary database connections
* Support real-time client synchronization
* Maintain a scalable backend architecture
* Provide secure user authentication and booking management

## Future Improvements

* Online payment integration
* QR-based ticket verification
* Email and SMS notifications
* Mobile application
* Advanced administration dashboard
* Booking history and analytics
* Automatic seat allocation
* Improved concurrency control
* Horizontal application scaling
* Application monitoring and logging

## Project Status

RailwaySheba is currently under active development.

Features, architecture, and implementation details may change as the project evolves.


## RailwaySheba

A real-time railway ticket booking platform built with Django, ASGI, WebSockets, Redis, PostgreSQL, and PgBouncer.
