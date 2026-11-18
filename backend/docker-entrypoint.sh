#!/bin/sh
# Backend container entrypoint.
#   serve (default) -> `alembic upgrade head` (retried while the database starts), then uvicorn.
#   anything else   -> executed as given (the laptop's docker-compose.yml passes its own
#                      `uvicorn ... --reload`; `docker compose exec`/`run` commands work as before).
#
# WEB_CONCURRENCY defaults to 1 on purpose: the live robot link (RobotHub, admin "Robot (live)",
# robot commands), the rate limiters, the daily paid-call counter's cache and the login lockout
# are in-process state; several workers would each hold their own copy and split them.
# FORWARDED_ALLOW_IPS: X-Forwarded-For/Proto are trusted only from these addresses (Caddy's fixed
# address and the Docker bridge gateway, i.e. a host nginx in "behind an existing web server" mode).
# --no-access-log: uvicorn's access log prints the full URL, and /ws/robot?token=... carries the
# robot token; the app logs its own per-turn lines (with latency) without it.
set -eu

if [ "$#" -eq 0 ] || [ "$1" = "serve" ]; then
    tries=0
    max_tries="${MIGRATE_TRIES:-15}"
    until alembic upgrade head; do
        tries=$((tries + 1))
        if [ "$tries" -ge "$max_tries" ]; then
            echo "entrypoint: alembic upgrade head failed $tries times, giving up" >&2
            exit 1
        fi
        echo "entrypoint: database not ready or migration failed (try $tries/$max_tries), retrying in 3 s" >&2
        sleep 3
    done
    exec uvicorn app.main:app \
        --host 0.0.0.0 --port 8000 \
        --workers "${WEB_CONCURRENCY:-1}" \
        --proxy-headers \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-172.31.240.10,172.31.240.1}" \
        --no-server-header \
        --no-access-log
fi

exec "$@"
