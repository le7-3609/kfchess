# Kung Fu Chess server image.
#
# Today the server is a single process serving both the WebSocket game socket
# and the HTTP API. Server_Design.md section 6.3 splits those into two roles;
# when that happens this file becomes two (or the same image with two entry
# commands), which is why the entry command lives in docker-compose.yml rather
# than being baked in as a hard-coded CMD.

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
COPY main_server.py ./

# The SQLite file must live on a mounted volume, not in the image layer, or the
# database is discarded every time the container is replaced.
RUN mkdir -p /data

# Nothing here needs root, and a container that runs as root makes any code
# execution bug considerably more expensive.
RUN useradd --create-home --uid 10001 kfchess && chown -R kfchess /app /data
USER kfchess

EXPOSE 8765 8080

CMD ["python", "main_server.py", "--host", "0.0.0.0", "--db-path", "/data/kfchess.db"]
