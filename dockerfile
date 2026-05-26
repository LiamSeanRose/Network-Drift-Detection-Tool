# Dockerfile — builds the netdrift application image (v0.2).
#
# One image holds the whole app (API + scheduler + pipeline). docker-compose
# runs it twice — once as the API, once as the scheduler — plus a separate
# Postgres image. See docker-compose.yml.

# Start from a slim official Python image matching the project's requirement
# (pyproject.toml: requires-python >=3.11).
FROM python:3.11-slim

# Don't write .pyc files, and don't buffer stdout — so container logs appear
# immediately (important for seeing scheduler/API output with `docker compose logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Everything happens under /app inside the container.
WORKDIR /app

# Copy only the packaging metadata first, install deps, THEN copy the code.
# This ordering lets Docker cache the (slow) dependency install and skip it on
# rebuilds where only source changed — a standard Docker layer-caching trick.
COPY pyproject.toml ./
COPY src ./src

# Alembic config + migration scripts, needed by the `migrate` compose service
# to run `alembic upgrade head` against the database inside the network.
COPY alembic.ini ./
COPY migrations ./migrations

# Install the package and its dependencies. No --break-system-packages needed:
# this is an isolated container, not a managed host OS.
RUN pip install --no-cache-dir .

# No CMD here on purpose: this image is a base for two roles. docker-compose
# supplies the command for each (uvicorn for the API, python -m for the
# scheduler), so one image serves both without baking in a single entrypoint.