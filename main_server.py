"""CLI entry point for the KungFu Chess multiplayer server."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.application.auth_service import AuthService
from server.application.game_query_service import GameQueryService
from server.infrastructure.database.database import Database
from server.presentation.http_api import DEFAULT_HTTP_PORT, HttpApiServer
from server.presentation.ws_server import DEFAULT_HOST, DEFAULT_PORT, KFChessServer

DEFAULT_DB_PATH = "kfchess.db"


async def _async_main(args: argparse.Namespace) -> None:
    """Own the Database connection's lifetime across the server's run."""
    database = Database(args.db_path)
    await database.connect()
    auth_service = AuthService(database)

    server = KFChessServer(
        host=args.host, port=args.port, database=database, auth_service=auth_service
    )
    http_api = HttpApiServer(
        GameQueryService(database), host=args.host, port=args.http_port
    )
    try:
        await http_api.start()
        await server.run_forever()
    finally:
        await server.stop()
        await http_api.stop()
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="KungFu Chess multiplayer server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="WebSocket bind port")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP API bind port")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite database file path")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        logging.info("Server stopped by user.")


if __name__ == "__main__":
    main()
