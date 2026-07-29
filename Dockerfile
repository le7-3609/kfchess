# Kung Fu Chess server image.
#
# One image, every role: `main_ws.py` (the game socket), `main_api.py` (the HTTP
# API), `main_server.py` (both, for local development) and `alembic upgrade
# head` (the pre-deploy migration job). The roles differ only in their entry
# command, which is why the command lives in the orchestration file rather than
# being baked in — the CMD below is the development default, and Compose and
# Kubernetes each override it per role.

FROM python:3.10-slim

# Bytecode writes and stdout buffering are both pure overhead in a container:
# the filesystem is ephemeral, and buffered logs arrive late or not at all when
# the process is killed.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies are copied and installed before the source so that editing code
# does not invalidate the (slow) pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY server/ ./server/
COPY main_server.py main_ws.py main_api.py ./

# The migration job runs from this same image, so the schema that ships is the
# schema the code in this layer was built against — a migration image that can
# drift from the application image is how a replica starts against a database it
# does not match.
COPY alembic.ini ./
COPY migrations/ ./migrations/

# The SQLite file must live on a mounted volume, not in the image layer, or the
# database is discarded every time the container is replaced.
RUN mkdir -p /data

# Nothing here needs root, and a container that runs as root makes any code
# execution bug considerably more expensive.
RUN useradd --create-home --uid 10001 kfchess && chown -R kfchess /app /data
USER kfchess

EXPOSE 8765 8080

CMD ["python", "main_server.py", "--host", "0.0.0.0", "--db-path", "/data/kfchess.db"]
