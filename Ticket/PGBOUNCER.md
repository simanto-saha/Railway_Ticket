# PgBouncer deployment notes

`pgbouncer.ini.example` is a deployment template for accepting up to 10,000 application-side client connections while keeping the PostgreSQL backend pool capped. It is not an assertion that this machine or database can sustain 10,000 simultaneous queries or 10,000 requests per second.

## Capacity model

- `max_client_conn = 10000` is the maximum number of client sockets PgBouncer accepts. It is not the number of PostgreSQL connections.
- `max_db_connections = 60` caps server connections for this database. This leaves headroom on the currently observed PostgreSQL configuration (`max_connections = 100`), but re-check the live connection budget before applying it. Reserve connections for administration, migrations, monitoring, and other applications.
- When all backend connections are busy, additional clients wait in PgBouncer's queue. Throughput still depends on query time, CPU, memory, storage, Django worker capacity, and request mix.
- Start with a small Locust test and increase traffic gradually while observing response latency, errors, PgBouncer wait time, and PostgreSQL resource usage. Do not send a 10,000-user burst to a production database without a staged capacity test.

## Apply the template

1. Review `pgbouncer.ini.example` and merge the pool values into the active config at `/etc/pgbouncer/pgbouncer.ini`. Do not overwrite its existing database mapping or authentication settings.
2. Keep the PgBouncer listener on `6432`, which is the port configured in the Django `.env`.
3. Set these values in the active `[pgbouncer]` section:

   ```ini
   pool_mode = transaction
   max_client_conn = 10000
   default_pool_size = 40
   min_pool_size = 5
   reserve_pool_size = 10
   reserve_pool_timeout = 3
   max_db_connections = 60
   max_user_connections = 60
   ```

   `max_db_connections=60` is based on the currently observed PostgreSQL `max_connections=100`; re-check that value and other services' use before applying. Do not raise it to 10,000.
4. Keep `/etc/pgbouncer/userlist.txt` and its current SCRAM credentials intact. Never commit passwords or verifier values to this repository.
5. The active service currently has `LimitNOFILE=524288`, which is above the 12,000 minimum used by the example. If this changes, raise the PgBouncer service's file-descriptor limit. For systemd, a service override can be:

   ```ini
   [Service]
   LimitNOFILE=12000
   ```

   Ensure the host's system-wide file descriptor limits also permit this value. PgBouncer needs descriptors for client sockets, backend sockets, logs, and its listener.
6. Validate the config with the installed PgBouncer binary, then restart the service during a maintenance window (restart briefly disconnects clients): `sudo systemctl restart pgbouncer`. The active service file is root-owned and not readable by the current workspace user, so these host-level edits/restart must be done by an administrator on the PgBouncer host.
7. Check PgBouncer's `SHOW POOLS;`, `SHOW STATS;`, and `SHOW CONFIG;` output during a gradual load test. A high `cl_waiting` count indicates that the backend pool is saturated; increase pool size only if PostgreSQL has safe connection and resource headroom.

Django is configured with `CONN_MAX_AGE=0` and `DISABLE_SERVER_SIDE_CURSORS=True`, which is appropriate for transaction pooling. Keep the app pointed at PgBouncer rather than connecting directly to PostgreSQL when using this configuration.
