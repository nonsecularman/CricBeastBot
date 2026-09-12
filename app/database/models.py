"""
SQLAlchemy ORM models.

Every important piece of game state lives here, never only in RAM, so a
process restart never corrupts an in-progress game.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class GameType(str, enum.Enum):
    SOLO = "solo"
    TEAM = "team"
    TOURNAMENT = "tournament"


class GameStatus(str, enum.Enum):
    QUEUE = "queue"          # waiting for players to /join
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BallResult(str, enum.Enum):
    RUNS = "runs"
    OUT = "out"


class TournamentStatus(str, enum.Enum):
    REGISTRATION = "registration"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# --------------------------------------------------------------------------- #
# Core identity tables
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    has_started_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stats: Mapped["PlayerStat"] = relationship(back_populates="user", uselist=False)

    @property
    def display_name(self) -> str:
        return f"@{self.username}" if self.username else self.first_name or str(self.id)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram chat id
    title: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Solo / generic game tables
# --------------------------------------------------------------------------- #
class Game(Base):
    """
    A single game instance. Used for solo games and as the innings engine
    underneath team games. One row = one live (or finished) match in a chat.
    """
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    game_type: Mapped[GameType] = mapped_column(Enum(GameType), default=GameType.SOLO)
    status: Mapped[GameStatus] = mapped_column(Enum(GameStatus), default=GameStatus.QUEUE)

    balls_per_over: Mapped[int] = mapped_column(Integer, default=6)
    created_by: Mapped[int] = mapped_column(BigInteger)

    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    current_batter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_bowler_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    current_over: Mapped[int] = mapped_column(Integer, default=0)
    balls_this_over: Mapped[int] = mapped_column(Integer, default=0)

    pending_batter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_bowler_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Team-game linkage (nullable for pure solo games)
    team_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("team_matches.id"), nullable=True
    )
    tournament_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("tournament_matches.id"), nullable=True
    )

    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    players: Mapped[list["GamePlayer"]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    balls: Mapped[list["Ball"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="Ball.id"
    )


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (UniqueConstraint("game_id", "user_id", name="uq_game_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(128), default="")

    batting_order: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    balls_faced: Mapped[int] = mapped_column(Integer, default=0)
    is_out: Mapped[bool] = mapped_column(Boolean, default=False)
    has_batted: Mapped[bool] = mapped_column(Boolean, default=False)

    overs_bowled: Mapped[int] = mapped_column(Integer, default=0)
    runs_conceded: Mapped[int] = mapped_column(Integer, default=0)
    wickets_taken: Mapped[int] = mapped_column(Integer, default=0)

    # True only for team-match innings: marks this row as belonging to the
    # fixed bowling-side roster, so turn rotation never hands the ball to a
    # teammate of the batting side. Always False for solo games (where any
    # non-batter may bowl).
    is_bowler_pool: Mapped[bool] = mapped_column(Boolean, default=False)

    joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped["Game"] = relationship(back_populates="players")


class Turn(Base):
    """One batter's innings within a game (their full stint until OUT)."""
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    batter_id: Mapped[int] = mapped_column(BigInteger)
    bowler_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ball(Base):
    __tablename__ = "balls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    over_number: Mapped[int] = mapped_column(Integer)
    ball_number: Mapped[int] = mapped_column(Integer)  # ball index within the over (1-based)

    batter_id: Mapped[int] = mapped_column(BigInteger)
    bowler_id: Mapped[int] = mapped_column(BigInteger)
    batter_number: Mapped[int] = mapped_column(Integer)
    bowler_number: Mapped[int] = mapped_column(Integer)

    result: Mapped[BallResult] = mapped_column(Enum(BallResult))
    runs: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped["Game"] = relationship(back_populates="balls")


# --------------------------------------------------------------------------- #
# Team game tables
# --------------------------------------------------------------------------- #
class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(64))
    captain_id: Mapped[int] = mapped_column(BigInteger)
    slot: Mapped[str] = mapped_column(String(1), default="A")  # "A" or "B" - fixed per chat
    cap_color: Mapped[str] = mapped_column(String(8), default="blue")  # "blue" or "red"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    players: Mapped[list["TeamPlayer"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamPlayer(Base):
    __tablename__ = "team_players"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    batting_order: Mapped[int] = mapped_column(Integer, default=0)
    bowling_order: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    team: Mapped["Team"] = relationship(back_populates="players")


class TeamMatch(Base):
    __tablename__ = "team_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    team_b_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    balls_per_over: Mapped[int] = mapped_column(Integer, default=6)
    status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.PENDING)
    current_innings: Mapped[int] = mapped_column(Integer, default=1)  # 1 or 2
    team_a_score: Mapped[int] = mapped_column(Integer, default=0)
    team_b_score: Mapped[int] = mapped_column(Integer, default=0)
    winner_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tournament_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("tournament_matches.id"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Tournament tables
# --------------------------------------------------------------------------- #
class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128))
    creator_id: Mapped[int] = mapped_column(BigInteger)
    max_participants: Mapped[int] = mapped_column(Integer, default=8)
    status: Mapped[TournamentStatus] = mapped_column(
        Enum(TournamentStatus), default=TournamentStatus.REGISTRATION
    )
    balls_per_over: Mapped[int] = mapped_column(Integer, default=6)
    participants: Mapped[list] = mapped_column(JSON, default=list)  # list of {id, name}
    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TournamentMatch(Base):
    __tablename__ = "tournament_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    round_number: Mapped[int] = mapped_column(Integer)  # 1 = quarterfinal-ish, increases
    round_name: Mapped[str] = mapped_column(String(32), default="")
    slot_index: Mapped[int] = mapped_column(Integer, default=0)
    player_a_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    player_b_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.PENDING)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Stats / Hall of Fame / Admin
# --------------------------------------------------------------------------- #
class PlayerStat(Base):
    __tablename__ = "player_stats"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    matches: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    wickets: Mapped[int] = mapped_column(Integer, default=0)
    highest_score: Mapped[int] = mapped_column(Integer, default=0)
    tournaments_won: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="stats")

    @property
    def win_rate(self) -> float:
        return round((self.wins / self.matches) * 100, 1) if self.matches else 0.0


class HallOfFameEntry(Base):
    """Denormalized, fast-to-query leaderboard snapshot row per player."""
    __tablename__ = "hall_of_fame"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    points: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
