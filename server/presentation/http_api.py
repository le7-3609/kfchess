"""HTTP API — read-only replay and leaderboard endpoints.

Layer: presentation (server/presentation)
Owns: aiohttp routing, request validation at the edge (fail fast on a bad id),
and formatting GameQueryService DTOs into JSON responses. Runs alongside the
WebSocket server in the same process on its own port.
Must not own: SQL (Database), row->DTO mapping (GameQueryService), or game
rules. Completed games are public, so these routes need no auth — but the
handlers stay thin so auth can be added at the edge later.
"""

import logging
from typing import Any, Dict

from aiohttp import web

from server.application.game_query_service import GameQueryService, GameReplay
from server.application.pgn_exporter import to_pgn

_LOGGER = logging.getLogger(__name__)

DEFAULT_HTTP_HOST = "localhost"
DEFAULT_HTTP_PORT = 8080


def _replay_to_dict(replay: GameReplay) -> Dict[str, Any]:
    """Shape a GameReplay DTO into the replay JSON body."""
    return {
        "game": {
            "game_id": replay.game_id,
            "room_id": replay.room_id,
            "white_username": replay.white_username,
            "black_username": replay.black_username,
            "winner_id": replay.winner_id,
            "result": replay.result,
            "white_elo_before": replay.white_elo_before,
            "white_elo_after": replay.white_elo_after,
            "black_elo_before": replay.black_elo_before,
            "black_elo_after": replay.black_elo_after,
            "started_at": replay.started_at,
            "ended_at": replay.ended_at,
        },
        "moves": [
            {
                "move_number": move.move_number,
                "from": move.from_square,
                "to": move.to_square,
                "piece": move.piece_type,
                "color": move.piece_color,
                "captured_piece": move.captured_piece,
                "timestamp": move.timestamp,
            }
            for move in replay.moves
        ],
    }


def _parse_game_id(request: web.Request) -> int:
    """Validate the {game_id} path segment, failing fast with 400 if non-integer."""
    raw = request.match_info["game_id"]
    try:
        return int(raw)
    except ValueError:
        raise web.HTTPBadRequest(reason=f"Invalid game id: {raw!r}")


class HttpApi:
    """Read-only HTTP endpoints backed by GameQueryService.

    Handlers are methods so they close over the query service without a module
    global; routing lives in `build_app`, mirroring how ws_server keeps its
    dispatch table in one place.
    """

    def __init__(self, query_service: GameQueryService) -> None:
        self._query_service = query_service

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/games/{game_id}", self.get_game)
        app.router.add_get("/api/games/{game_id}/pgn", self.get_game_pgn)
        app.router.add_get("/api/leaderboard", self.get_leaderboard)
        return app

    async def get_game(self, request: web.Request) -> web.Response:
        game_id = _parse_game_id(request)
        replay = await self._query_service.get_replay(game_id)
        if replay is None:
            raise web.HTTPNotFound(reason=f"No game with id {game_id}")
        return web.json_response(_replay_to_dict(replay))

    async def get_game_pgn(self, request: web.Request) -> web.Response:
        game_id = _parse_game_id(request)
        replay = await self._query_service.get_replay(game_id)
        if replay is None:
            raise web.HTTPNotFound(reason=f"No game with id {game_id}")
        return web.Response(
            text=to_pgn(replay),
            content_type="application/x-chess-pgn",
            headers={"Content-Disposition": f'attachment; filename="{replay.room_id}.pgn"'},
        )

    async def get_leaderboard(self, request: web.Request) -> web.Response:
        rows = await self._query_service.get_leaderboard()
        body = [
            {"username": row.username, "elo": row.elo, "total_games": row.total_games, "wins": row.wins}
            for row in rows
        ]
        return web.json_response(body)


class HttpApiServer:
    """Owns the aiohttp app's runtime lifecycle alongside the WS server."""

    def __init__(
        self,
        query_service: GameQueryService,
        host: str = DEFAULT_HTTP_HOST,
        port: int = DEFAULT_HTTP_PORT,
    ) -> None:
        self._api = HttpApi(query_service)
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._api.build_app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        _LOGGER.info("HTTP API running on http://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        _LOGGER.info("HTTP API stopped")
