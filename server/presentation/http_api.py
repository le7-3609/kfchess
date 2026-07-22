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

# Routes. The {game_id} placeholder name must match _parse_game_id's lookup.
_PATH_PARAM_GAME_ID = "game_id"
ROUTE_GAME = "/api/games/{game_id}"
ROUTE_GAME_PGN = "/api/games/{game_id}/pgn"
ROUTE_LEADERBOARD = "/api/leaderboard"

PGN_CONTENT_TYPE = "application/x-chess-pgn"
_HEADER_CONTENT_DISPOSITION = "Content-Disposition"

# Replay JSON body keys.
_KEY_GAME = "game"
_KEY_MOVES = "moves"
_KEY_GAME_ID = "game_id"
_KEY_ROOM_ID = "room_id"
_KEY_WHITE_USERNAME = "white_username"
_KEY_BLACK_USERNAME = "black_username"
_KEY_WINNER_ID = "winner_id"
_KEY_RESULT = "result"
_KEY_WHITE_ELO_BEFORE = "white_elo_before"
_KEY_WHITE_ELO_AFTER = "white_elo_after"
_KEY_BLACK_ELO_BEFORE = "black_elo_before"
_KEY_BLACK_ELO_AFTER = "black_elo_after"
_KEY_STARTED_AT = "started_at"
_KEY_ENDED_AT = "ended_at"
_KEY_MOVE_NUMBER = "move_number"
_KEY_FROM = "from"
_KEY_TO = "to"
_KEY_PIECE = "piece"
_KEY_COLOR = "color"
_KEY_CAPTURED_PIECE = "captured_piece"
_KEY_TIMESTAMP = "timestamp"

# Leaderboard JSON body keys.
_KEY_USERNAME = "username"
_KEY_ELO = "elo"
_KEY_TOTAL_GAMES = "total_games"
_KEY_WINS = "wins"


def _replay_to_dict(replay: GameReplay) -> Dict[str, Any]:
    """Shape a GameReplay DTO into the replay JSON body."""
    return {
        _KEY_GAME: {
            _KEY_GAME_ID: replay.game_id,
            _KEY_ROOM_ID: replay.room_id,
            _KEY_WHITE_USERNAME: replay.white_username,
            _KEY_BLACK_USERNAME: replay.black_username,
            _KEY_WINNER_ID: replay.winner_id,
            _KEY_RESULT: replay.result,
            _KEY_WHITE_ELO_BEFORE: replay.white_elo_before,
            _KEY_WHITE_ELO_AFTER: replay.white_elo_after,
            _KEY_BLACK_ELO_BEFORE: replay.black_elo_before,
            _KEY_BLACK_ELO_AFTER: replay.black_elo_after,
            _KEY_STARTED_AT: replay.started_at,
            _KEY_ENDED_AT: replay.ended_at,
        },
        _KEY_MOVES: [
            {
                _KEY_MOVE_NUMBER: move.move_number,
                _KEY_FROM: move.from_square,
                _KEY_TO: move.to_square,
                _KEY_PIECE: move.piece_type,
                _KEY_COLOR: move.piece_color,
                _KEY_CAPTURED_PIECE: move.captured_piece,
                _KEY_TIMESTAMP: move.timestamp,
            }
            for move in replay.moves
        ],
    }


def _parse_game_id(request: web.Request) -> int:
    """Validate the {game_id} path segment, failing fast with 400 if non-integer."""
    raw = request.match_info[_PATH_PARAM_GAME_ID]
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
        app.router.add_get(ROUTE_GAME, self.get_game)
        app.router.add_get(ROUTE_GAME_PGN, self.get_game_pgn)
        app.router.add_get(ROUTE_LEADERBOARD, self.get_leaderboard)
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
            content_type=PGN_CONTENT_TYPE,
            headers={_HEADER_CONTENT_DISPOSITION: f'attachment; filename="{replay.room_id}.pgn"'},
        )

    async def get_leaderboard(self, request: web.Request) -> web.Response:
        rows = await self._query_service.get_leaderboard()
        body = [
            {
                _KEY_USERNAME: row.username,
                _KEY_ELO: row.elo,
                _KEY_TOTAL_GAMES: row.total_games,
                _KEY_WINS: row.wins,
            }
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
