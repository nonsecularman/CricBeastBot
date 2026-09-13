"""
Core game engine for CricBeastBot.

This module is the single source of truth for solo-game state transitions.
Team games reuse it per-innings (see app/game/team.py). Every function here
validates chat/game/player/turn before mutating anything, so a stray or
malicious callback can never corrupt another player's turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Ball, BallResult, Game, GamePlayer, GameStatus, GameType, Turn
from app.game import turns as turn_logic
from app.game.scoring import is_boundary_four, is_boundary_six, resolve_ball


# --------------------------------------------------------------------------- #
# Errors - these are the anti-cheat / validation boundary.
# --------------------------------------------------------------------------- #
class GameError(Exception):
    """Base class for all engine-level rejections."""


class NoActiveGame(GameError):
    pass


class GameAlreadyStarted(GameError):
    pass


class GameNotInProgress(GameError):
    pass


class AlreadyJoined(GameError):
    pass


class NotInGame(GameError):
    pass


class QueueFull(GameError):
    pass


class NotYourTurn(GameError):
    pass


class AlreadySubmitted(GameError):
    pass


class NotAuthorized(GameError):
    pass


# --------------------------------------------------------------------------- #
# Queue management
# --------------------------------------------------------------------------- #
async def get_active_game(session: AsyncSession, chat_id: int, game_type: GameType = GameType.SOLO) -> Game | None:
    result = await session.execute(
        select(Game)
        .where(
            Game.chat_id == chat_id,
            Game.game_type == game_type,
            Game.status.in_([GameStatus.QUEUE, GameStatus.IN_PROGRESS]),
        )
        .order_by(Game.id.desc())
    )
    return result.scalars().first()


async def get_game_awaiting_batter(session: AsyncSession, chat_id: int, user_id: int) -> Game | None:
    """
    Finds the in-progress game (any type - solo or team) in this chat where
    it's currently user_id's turn to bat. Used so a plain "1"-"6" text
    message typed in the group can be treated as the batter's shot, without
    needing inline buttons.
    """
    result = await session.execute(
        select(Game)
        .where(
            Game.chat_id == chat_id,
            Game.status == GameStatus.IN_PROGRESS,
            Game.current_batter_id == user_id,
        )
        .order_by(Game.id.desc())
    )
    return result.scalars().first()


async def create_solo_game(session: AsyncSession, chat_id: int, creator_id: int, balls_per_over: int) -> Game:
    existing = await get_active_game(session, chat_id, GameType.SOLO)
    if existing is not None:
        raise GameAlreadyStarted("A solo game is already active in this chat.")
    game = Game(
        chat_id=chat_id,
        game_type=GameType.SOLO,
        status=GameStatus.QUEUE,
        balls_per_over=balls_per_over,
        created_by=creator_id,
    )
    session.add(game)
    await session.flush()
    return game


async def _load_players(session: AsyncSession, game_id: int) -> list[GamePlayer]:
    result = await session.execute(
        select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.batting_order)
    )
    return list(result.scalars().all())


async def join_game(session: AsyncSession, game: Game, user_id: int, display_name: str) -> GamePlayer:
    if game.status != GameStatus.QUEUE:
        raise GameAlreadyStarted("This game has already started - you can't join now.")
    players = await _load_players(session, game.id)
    if len(players) >= settings.solo_max_players:
        raise QueueFull(f"Queue is full (max {settings.solo_max_players} players).")
    if any(p.user_id == user_id for p in players):
        raise AlreadyJoined("You're already in the queue.")
    player = GamePlayer(
        game_id=game.id,
        user_id=user_id,
        display_name=display_name,
        batting_order=len(players),
    )
    session.add(player)
    await session.flush()
    return player


async def leave_game(session: AsyncSession, game: Game, user_id: int) -> None:
    if game.status != GameStatus.QUEUE:
        raise GameAlreadyStarted("Game already started - you can no longer leave the queue.")
    players = await _load_players(session, game.id)
    player = next((p for p in players if p.user_id == user_id), None)
    if player is None:
        raise NotInGame("You're not in this queue.")
    await session.delete(player)
    await session.flush()


def can_force_start(players: list[GamePlayer]) -> bool:
    return len(players) >= 2


async def start_game(session: AsyncSession, game: Game) -> tuple[GamePlayer, GamePlayer]:
    if game.status != GameStatus.QUEUE:
        raise GameAlreadyStarted("Game already started.")
    players = await _load_players(session, game.id)
    if len(players) < 2:
        raise GameError("Need at least 2 players to start.")

    game.status = GameStatus.IN_PROGRESS
    game.current_over = 1
    game.balls_this_over = 0
    game.pending_batter_number = None
    game.pending_bowler_number = None

    batter = turn_logic.next_batter(players, None)
    bowler = turn_logic.next_bowler(players, batter.user_id, None)
    game.current_batter_id = batter.user_id
    game.current_bowler_id = bowler.user_id

    session.add(Turn(game_id=game.id, batter_id=batter.user_id, bowler_id=bowler.user_id))
    await session.flush()
    return batter, bowler


# --------------------------------------------------------------------------- #
# Ball-by-ball gameplay
# --------------------------------------------------------------------------- #
@dataclass
class BallResolution:
    ball: Ball
    batter: GamePlayer
    bowler: GamePlayer
    is_wicket: bool
    is_boundary_four: bool
    is_boundary_six: bool
    is_over_complete: bool
    is_game_over: bool
    next_batter: GamePlayer | None = None
    next_bowler: GamePlayer | None = None
    winner: GamePlayer | None = None
    all_players: list[GamePlayer] = field(default_factory=list)


async def submit_batter_number(session: AsyncSession, game: Game, user_id: int, number: int) -> BallResolution | None:
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    if game.locked:
        raise NotAuthorized("This game is locked by an admin right now.")
    if game.current_batter_id != user_id:
        raise NotYourTurn("It's not your turn to bat.")
    if game.pending_batter_number is not None:
        raise AlreadySubmitted("You've already made your shot for this ball.")
    game.pending_batter_number = number
    await session.flush()
    return await _try_resolve(session, game)


async def submit_bowler_number(session: AsyncSession, game: Game, user_id: int, number: int) -> BallResolution | None:
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    if game.locked:
        raise NotAuthorized("This game is locked by an admin right now.")
    if game.current_bowler_id != user_id:
        raise NotYourTurn("It's not your turn to bowl.")
    if game.pending_bowler_number is not None:
        raise AlreadySubmitted("Your bowl is already locked in for this ball.")
    game.pending_bowler_number = number
    await session.flush()
    return await _try_resolve(session, game)


async def _try_resolve(session: AsyncSession, game: Game) -> BallResolution | None:
    if game.pending_batter_number is None or game.pending_bowler_number is None:
        return None  # still waiting on the other player

    players = await _load_players(session, game.id)
    by_id = {p.user_id: p for p in players}
    batter = by_id[game.current_batter_id]
    bowler = by_id[game.current_bowler_id]

    outcome = resolve_ball(game.pending_batter_number, game.pending_bowler_number)

    ball = Ball(
        game_id=game.id,
        over_number=game.current_over,
        ball_number=game.balls_this_over + 1,
        batter_id=batter.user_id,
        bowler_id=bowler.user_id,
        batter_number=game.pending_batter_number,
        bowler_number=game.pending_bowler_number,
        result=outcome.result,
        runs=outcome.runs,
    )
    session.add(ball)

    batter.balls_faced += 1
    is_wicket = outcome.result == BallResult.OUT
    four = False
    six = False
    if is_wicket:
        batter.is_out = True
        batter.has_batted = True
        bowler.wickets_taken += 1
    else:
        batter.runs += outcome.runs
        bowler.runs_conceded += outcome.runs
        four = is_boundary_four(outcome.runs)
        six = is_boundary_six(outcome.runs)

    game.balls_this_over += 1
    game.pending_batter_number = None
    game.pending_bowler_number = None

    is_over_complete = game.balls_this_over >= game.balls_per_over
    next_batter: GamePlayer | None = None
    next_bowler: GamePlayer | None = None
    is_game_over = False
    winner: GamePlayer | None = None

    if is_wicket:
        batter.has_batted = True
        # The bowler who just took the wicket gets to bat next (falls back
        # to normal queue order once that bowler has already had a turn).
        candidate = turn_logic.next_batter_after_wicket(players, bowler.user_id)
        if candidate is None:
            is_game_over = True
        else:
            next_batter = candidate
            game.current_batter_id = candidate.user_id

    if is_over_complete:
        bowler.overs_bowled += 1
        game.current_over += 1
        game.balls_this_over = 0

    # Bowler rotation: needed whenever the over just completed, OR whenever
    # a wicket changed who's batting - either way the previous bowler can't
    # (or shouldn't) keep bowling to whoever's up next.
    if not is_game_over and (is_over_complete or next_batter is not None):
        active_batter_id = next_batter.user_id if next_batter else batter.user_id
        candidate_bowler = turn_logic.next_bowler(players, active_batter_id, bowler.user_id)
        if candidate_bowler is not None:
            next_bowler = candidate_bowler
            game.current_bowler_id = candidate_bowler.user_id

    if not is_game_over and turn_logic.all_batters_done(players):
        is_game_over = True

    if is_game_over:
        game.status = GameStatus.COMPLETED
        alive = [p for p in players]
        winner = max(alive, key=lambda p: p.runs) if alive else None
        game.winner_id = winner.user_id if winner else None
    elif next_batter is not None:
        session.add(
            Turn(
                game_id=game.id,
                batter_id=next_batter.user_id,
                bowler_id=(next_bowler.user_id if next_bowler else bowler.user_id),
            )
        )

    await session.flush()

    return BallResolution(
        ball=ball,
        batter=batter,
        bowler=bowler,
        is_wicket=is_wicket,
        is_boundary_four=four,
        is_boundary_six=six,
        is_over_complete=is_over_complete,
        is_game_over=is_game_over,
        next_batter=next_batter,
        next_bowler=next_bowler,
        winner=winner,
        all_players=players,
    )


async def cancel_game(session: AsyncSession, game: Game) -> None:
    game.status = GameStatus.CANCELLED
    await session.flush()


async def get_players(session: AsyncSession, game_id: int) -> list[GamePlayer]:
    return await _load_players(session, game_id)


async def get_game_by_id(session: AsyncSession, game_id: int) -> Game | None:
    return await session.get(Game, game_id)


# --------------------------------------------------------------------------- #
# Owner / admin cheat-panel overrides.
# These bypass the normal pending-number handshake entirely - they are
# privileged shortcuts and every call site MUST verify is_owner() first.
# --------------------------------------------------------------------------- #
async def owner_force_wicket(session: AsyncSession, game: Game) -> BallResolution:
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    players = await _load_players(session, game.id)
    by_id = {p.user_id: p for p in players}
    batter = by_id[game.current_batter_id]
    bowler = by_id[game.current_bowler_id]

    ball = Ball(
        game_id=game.id, over_number=game.current_over, ball_number=game.balls_this_over + 1,
        batter_id=batter.user_id, bowler_id=bowler.user_id,
        batter_number=0, bowler_number=0, result=BallResult.OUT, runs=0,
    )
    session.add(ball)
    batter.is_out = True
    batter.has_batted = True
    bowler.wickets_taken += 1
    game.balls_this_over += 1
    game.pending_batter_number = None
    game.pending_bowler_number = None

    is_over_complete = game.balls_this_over >= game.balls_per_over
    candidate = turn_logic.next_batter_after_wicket(players, bowler.user_id)
    is_game_over = candidate is None
    next_bowler = None
    if is_over_complete:
        bowler.overs_bowled += 1
        game.current_over += 1
        game.balls_this_over = 0
    if not is_game_over:
        next_bowler = turn_logic.next_bowler(players, candidate.user_id, bowler.user_id)
        if next_bowler:
            game.current_bowler_id = next_bowler.user_id

    winner = None
    if is_game_over:
        game.status = GameStatus.COMPLETED
        winner = max(players, key=lambda p: p.runs)
        game.winner_id = winner.user_id
    else:
        game.current_batter_id = candidate.user_id
        session.add(Turn(game_id=game.id, batter_id=candidate.user_id, bowler_id=game.current_bowler_id))

    await session.flush()
    return BallResolution(
        ball=ball, batter=batter, bowler=bowler, is_wicket=True, is_boundary_four=False,
        is_boundary_six=False, is_over_complete=is_over_complete, is_game_over=is_game_over,
        next_batter=candidate if not is_game_over else None, next_bowler=next_bowler,
        winner=winner, all_players=players,
    )


async def owner_add_runs(session: AsyncSession, game: Game, runs: int) -> GamePlayer:
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    players = await _load_players(session, game.id)
    batter = next(p for p in players if p.user_id == game.current_batter_id)
    batter.runs = max(0, batter.runs + runs)
    await session.flush()
    return batter


async def owner_skip_ball(session: AsyncSession, game: Game) -> bool:
    """Advances one ball as a dot-ball (no runs, no wicket). Returns True if that completed the over."""
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    players = await _load_players(session, game.id)
    bowler = next(p for p in players if p.user_id == game.current_bowler_id)
    ball = Ball(
        game_id=game.id, over_number=game.current_over, ball_number=game.balls_this_over + 1,
        batter_id=game.current_batter_id, bowler_id=game.current_bowler_id,
        batter_number=0, bowler_number=-1, result=BallResult.RUNS, runs=0,
    )
    session.add(ball)
    game.balls_this_over += 1
    over_complete = game.balls_this_over >= game.balls_per_over
    if over_complete:
        bowler.overs_bowled += 1
        game.current_over += 1
        game.balls_this_over = 0
        nxt = turn_logic.next_bowler(players, game.current_batter_id, bowler.user_id)
        if nxt:
            game.current_bowler_id = nxt.user_id
    await session.flush()
    return over_complete


async def owner_skip_over(session: AsyncSession, game: Game) -> None:
    if game.status != GameStatus.IN_PROGRESS:
        raise GameNotInProgress("This game isn't in progress.")
    while game.balls_this_over < game.balls_per_over:
        await owner_skip_ball(session, game)


async def owner_end_game(session: AsyncSession, game: Game) -> GamePlayer | None:
    players = await _load_players(session, game.id)
    game.status = GameStatus.COMPLETED
    winner = max(players, key=lambda p: p.runs) if players else None
    game.winner_id = winner.user_id if winner else None
    await session.flush()
    return winner


async def owner_reset_game(session: AsyncSession, game: Game) -> None:
    game.status = GameStatus.CANCELLED
    await session.flush()


async def owner_set_current_batter(session: AsyncSession, game: Game, new_batter_id: int) -> GamePlayer:
    players = await _load_players(session, game.id)
    target = next((p for p in players if p.user_id == new_batter_id), None)
    if target is None:
        raise NotInGame("That player isn't in this game.")
    if target.is_out:
        raise GameError("That player is already out.")
    game.current_batter_id = new_batter_id
    await session.flush()
    return target


async def owner_set_lock(session: AsyncSession, game: Game, locked: bool) -> None:
    game.locked = locked
    await session.flush()
