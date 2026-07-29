"""Initial schema: users, games, moves, game_statistics.

Revision ID: 0001
Revises:
Created: 2026-07-28

The PostgreSQL rendering of the four tables `Database.connect` used to create
with `CREATE TABLE IF NOT EXISTS` on every startup. That was correct for a file
one process owned and wrong for a database fifty replicas start against at once;
this runs once, from a job, before any replica does.

Differences from the SQLite definitions, all forced by the port:

* `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `BIGSERIAL PRIMARY KEY`. Bigint
  rather than int because `moves` gets a row per move of every game ever played,
  and 2.1 billion is a number this design's traffic reaches.
* `TIMESTAMP` becomes `TIMESTAMPTZ`. A naive timestamp is ambiguous the moment
  two replicas run in two regions, which is the whole premise of the deployment.
* `room_id` keeps its UNIQUE constraint, which is what makes
  `ON CONFLICT (room_id) DO NOTHING` an idempotent save rather than a duplicate
  game.
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("elo", sa.Integer, nullable=False, server_default="1200"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Completed games only — active rooms live in memory on a game authority.
    # winner_id is NULL for a draw; result stores the terminal reason
    # (checkmate, stalemate, timeout, ...).
    op.create_table(
        "games",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Text, nullable=False, unique=True),
        sa.Column(
            "white_player_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "black_player_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("winner_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("white_elo_before", sa.Integer, nullable=False),
        sa.Column("white_elo_after", sa.Integer, nullable=False),
        sa.Column("black_elo_before", sa.Integer, nullable=False),
        sa.Column("black_elo_after", sa.Integer, nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # One row per resolved move. timestamp is the game-clock instant the move
    # landed (ms elapsed), taken straight from MovesLog, so replay timing
    # survives.
    op.create_table(
        "moves",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "game_id",
            sa.BigInteger,
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("move_number", sa.Integer, nullable=False),
        sa.Column("from_square", sa.Text, nullable=False),
        sa.Column("to_square", sa.Text, nullable=False),
        sa.Column("piece_type", sa.Text, nullable=False),
        sa.Column("piece_color", sa.Text, nullable=False),
        sa.Column("captured_piece", sa.Text, nullable=True),
        sa.Column("timestamp", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Denormalized per-player cache, always recomputed from the games table
    # inside the same transaction that inserts a game — never incremented in
    # place, so it can never drift from the source of truth.
    op.create_table(
        "game_statistics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("total_games", sa.Integer, nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer, nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer, nullable=False, server_default="0"),
        sa.Column("elo_peak", sa.Integer, nullable=True),
        sa.Column("elo_low", sa.Integer, nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_index("idx_games_white_player", "games", ["white_player_id"])
    op.create_index("idx_games_black_player", "games", ["black_player_id"])
    op.create_index("idx_games_started_at", "games", ["started_at"])
    op.create_index("idx_moves_game_id", "moves", ["game_id"])
    op.create_index("idx_moves_created_at", "moves", ["created_at"])
    # The leaderboard orders by ELO descending; the index keeps top-N cheap.
    op.create_index("idx_users_elo", "users", ["elo"])


def downgrade() -> None:
    op.drop_table("game_statistics")
    op.drop_table("moves")
    op.drop_table("games")
    op.drop_table("users")
