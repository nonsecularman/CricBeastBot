"""Hall of Fame + player-stats update logic."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import HallOfFameEntry, PlayerStat, User

POINTS_PER_WIN = 25
POINTS_PER_RUN = 1
POINTS_PER_WICKET = 10
POINTS_PER_TOURNAMENT_WIN = 100


async def _get_or_create_stat(session: AsyncSession, user_id: int) -> PlayerStat:
    stat = await session.get(PlayerStat, user_id)
    if stat is None:
        stat = PlayerStat(user_id=user_id)
        session.add(stat)
        await session.flush()
    return stat


async def apply_match_result(
    session: AsyncSession,
    *,
    user_id: int,
    display_name: str,
    runs_scored: int,
    wickets_taken: int,
    won: bool,
    highest_score_candidate: int,
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, first_name=display_name)
        session.add(user)
        await session.flush()

    stat = await _get_or_create_stat(session, user_id)
    stat.matches += 1
    stat.wins += 1 if won else 0
    stat.losses += 0 if won else 1
    stat.runs += runs_scored
    stat.wickets += wickets_taken
    stat.highest_score = max(stat.highest_score, highest_score_candidate)
    stat.points += (
        runs_scored * POINTS_PER_RUN
        + wickets_taken * POINTS_PER_WICKET
        + (POINTS_PER_WIN if won else 0)
    )
    await session.flush()
    await _sync_hall_of_fame(session, user_id, display_name, stat.points)


async def apply_tournament_win(session: AsyncSession, user_id: int, display_name: str) -> None:
    stat = await _get_or_create_stat(session, user_id)
    stat.tournaments_won += 1
    stat.points += POINTS_PER_TOURNAMENT_WIN
    await session.flush()
    await _sync_hall_of_fame(session, user_id, display_name, stat.points)


async def _sync_hall_of_fame(session: AsyncSession, user_id: int, display_name: str, points: int) -> None:
    entry = await session.get(HallOfFameEntry, user_id)
    if entry is None:
        entry = HallOfFameEntry(user_id=user_id, display_name=display_name, points=points)
        session.add(entry)
    else:
        entry.points = points
        if display_name:
            entry.display_name = display_name
    await session.flush()


async def top_players(session: AsyncSession, limit: int = 10) -> list[HallOfFameEntry]:
    result = await session.execute(
        select(HallOfFameEntry).order_by(HallOfFameEntry.points.desc()).limit(limit)
    )
    return list(result.scalars().all())
