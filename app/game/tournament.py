"""
Tournament system: registration, knockout bracket generation, and per-match
progression. Each bracket match is played as a 2-innings 1-vs-1 mini match
using the same ball-by-ball engine as Solo/Team Game (one player bats while
the other bowls, then they swap; higher total wins and advances).
"""
from __future__ import annotations

import math
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Game,
    GamePlayer,
    GameStatus,
    GameType,
    MatchStatus,
    Tournament,
    TournamentMatch,
    TournamentStatus,
)
from app.game import engine


class TournamentError(Exception):
    pass


ROUND_NAMES = {1: "Final", 2: "Semi Final", 3: "Quarter Final"}


def _round_name(rounds_remaining: int) -> str:
    return ROUND_NAMES.get(rounds_remaining, f"Round of {2 ** rounds_remaining}")


async def create_tournament(
    session: AsyncSession, chat_id: int, name: str, creator_id: int, max_participants: int, balls_per_over: int
) -> Tournament:
    if max_participants < 2 or max_participants > 32:
        raise TournamentError("Max participants must be between 2 and 32.")
    t = Tournament(
        chat_id=chat_id,
        name=name,
        creator_id=creator_id,
        max_participants=max_participants,
        balls_per_over=balls_per_over,
        participants=[],
    )
    session.add(t)
    await session.flush()
    return t


async def list_open_tournaments(session: AsyncSession, chat_id: int) -> list[Tournament]:
    result = await session.execute(
        select(Tournament).where(
            Tournament.chat_id == chat_id, Tournament.status == TournamentStatus.REGISTRATION
        )
    )
    return list(result.scalars().all())


async def get_tournament(session: AsyncSession, tournament_id: int) -> Tournament | None:
    return await session.get(Tournament, tournament_id)


async def join_tournament(session: AsyncSession, t: Tournament, user_id: int, display_name: str) -> None:
    if t.status != TournamentStatus.REGISTRATION:
        raise TournamentError("Registration for this tournament has closed.")
    participants = list(t.participants or [])
    if any(p["id"] == user_id for p in participants):
        raise TournamentError("You're already registered for this tournament.")
    if len(participants) >= t.max_participants:
        raise TournamentError("This tournament is full.")
    participants.append({"id": user_id, "name": display_name})
    t.participants = participants
    await session.flush()


async def find_active_tournament_for_user(session: AsyncSession, chat_id: int, user_id: int) -> Tournament | None:
    result = await session.execute(
        select(Tournament).where(
            Tournament.chat_id == chat_id,
            Tournament.status.in_([TournamentStatus.REGISTRATION, TournamentStatus.IN_PROGRESS]),
        )
    )
    for t in result.scalars().all():
        if t.creator_id == user_id or any(p["id"] == user_id for p in (t.participants or [])):
            return t
    return None


async def start_tournament(session: AsyncSession, t: Tournament) -> list[TournamentMatch]:
    if t.status != TournamentStatus.REGISTRATION:
        raise TournamentError("This tournament has already started.")
    participants = list(t.participants or [])
    if len(participants) < 2:
        raise TournamentError("Need at least 2 registered players to start.")

    random.shuffle(participants)
    bracket_size = 2 ** math.ceil(math.log2(len(participants)))
    # Pad with byes (None) so the bracket is a full power-of-two tree.
    while len(participants) < bracket_size:
        participants.append(None)

    total_rounds = int(math.log2(bracket_size))
    matches: list[TournamentMatch] = []
    for i in range(0, bracket_size, 2):
        a = participants[i]
        b = participants[i + 1]
        m = TournamentMatch(
            tournament_id=t.id,
            round_number=1,
            round_name=_round_name(total_rounds),
            slot_index=i // 2,
            player_a_id=a["id"] if a else None,
            player_b_id=b["id"] if b else None,
        )
        # Auto-advance byes immediately.
        if a and not b:
            m.winner_id = a["id"]
            m.status = MatchStatus.COMPLETED
        elif b and not a:
            m.winner_id = b["id"]
            m.status = MatchStatus.COMPLETED
        matches.append(m)
        session.add(m)
    # Pre-create empty placeholder matches for subsequent rounds.
    round_size = bracket_size // 2
    round_number = 2
    while round_size >= 1:
        for slot in range(round_size // 2 if round_size > 1 else 1):
            if round_size == 1:
                break
            session.add(
                TournamentMatch(
                    tournament_id=t.id,
                    round_number=round_number,
                    round_name=_round_name(total_rounds - round_number + 1),
                    slot_index=slot,
                )
            )
        round_size //= 2
        round_number += 1

    t.status = TournamentStatus.IN_PROGRESS
    await session.flush()
    return matches


async def get_round_matches(session: AsyncSession, tournament_id: int, round_number: int) -> list[TournamentMatch]:
    result = await session.execute(
        select(TournamentMatch)
        .where(TournamentMatch.tournament_id == tournament_id, TournamentMatch.round_number == round_number)
        .order_by(TournamentMatch.slot_index)
    )
    return list(result.scalars().all())


async def next_playable_match(session: AsyncSession, tournament_id: int) -> TournamentMatch | None:
    """First match with both players known and not yet started/completed."""
    result = await session.execute(
        select(TournamentMatch)
        .where(TournamentMatch.tournament_id == tournament_id, TournamentMatch.status == MatchStatus.PENDING)
        .order_by(TournamentMatch.round_number, TournamentMatch.slot_index)
    )
    for m in result.scalars().all():
        if m.player_a_id is not None and m.player_b_id is not None:
            return m
    return None


async def _populate_solo_innings(session: AsyncSession, game: Game, batter_id: int, batter_name: str, bowler_id: int, bowler_name: str) -> None:
    session.add(GamePlayer(game_id=game.id, user_id=batter_id, display_name=batter_name, batting_order=0))
    session.add(
        GamePlayer(
            game_id=game.id, user_id=bowler_id, display_name=bowler_name,
            batting_order=9000, has_batted=True, is_bowler_pool=True,
        )
    )
    await session.flush()


async def start_match_innings(
    session: AsyncSession,
    tmatch: TournamentMatch,
    chat_id: int,
    balls_per_over: int,
    batter_id: int,
    batter_name: str,
    bowler_id: int,
    bowler_name: str,
) -> tuple[Game, GamePlayer, GamePlayer]:
    game = Game(
        chat_id=chat_id,
        game_type=GameType.TOURNAMENT,
        status=GameStatus.QUEUE,
        balls_per_over=balls_per_over,
        created_by=0,
        tournament_match_id=tmatch.id,
    )
    session.add(game)
    await session.flush()
    await _populate_solo_innings(session, game, batter_id, batter_name, bowler_id, bowler_name)
    batter, bowler = await engine.start_game(session, game)
    tmatch.status = MatchStatus.IN_PROGRESS
    await session.flush()
    return game, batter, bowler


async def _place_in_next_round(session: AsyncSession, tmatch: TournamentMatch, winner_id: int) -> TournamentMatch | None:
    """Places a match's winner into the next round's match slot. Returns that match, or None if tmatch was the final."""
    next_round = tmatch.round_number + 1
    next_slot = tmatch.slot_index // 2
    result = await session.execute(
        select(TournamentMatch).where(
            TournamentMatch.tournament_id == tmatch.tournament_id,
            TournamentMatch.round_number == next_round,
            TournamentMatch.slot_index == next_slot,
        )
    )
    nxt = result.scalars().first()
    if nxt is None:
        return None  # tmatch was the final
    if tmatch.slot_index % 2 == 0:
        nxt.player_a_id = winner_id
    else:
        nxt.player_b_id = winner_id
    await session.flush()
    return nxt


async def advance_winner(session: AsyncSession, tmatch: TournamentMatch, winner_id: int) -> TournamentMatch | None:
    """Marks tmatch won and places the winner into the next round's match."""
    tmatch.winner_id = winner_id
    tmatch.status = MatchStatus.COMPLETED
    await session.flush()
    return await _place_in_next_round(session, tmatch, winner_id)


def is_final(session_match: TournamentMatch, total_rounds_for_tournament: int) -> bool:
    return session_match.round_name == "Final"
