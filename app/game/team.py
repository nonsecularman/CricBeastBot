"""
Team Game: team creation/joining, and match orchestration.

Each innings of a team match is played using the exact same ball-by-ball
engine as Solo Game (app/game/engine.py) - a Game row is created per innings
with the batting side's roster as batters (in order) and the bowling side's
roster flagged `is_bowler_pool=True` so the turn-rotation logic in
app/game/turns.py never hands the ball to a batting-side teammate.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Game,
    GamePlayer,
    GameStatus,
    GameType,
    MatchStatus,
    Team,
    TeamHost,
    TeamMatch,
    TeamPlayer,
)
from app.game import engine

MAX_TEAM_SIZE = 11

CAP_LABELS = {"blue": "🔵 Blue Cap", "red": "🔴 Red Cap"}
CAP_EMOJI = {"blue": "🔵", "red": "🔴"}
CAP_BY_SLOT = {"A": "blue", "B": "red"}  # fixed: Team A is always Blue Cap, Team B is always Red Cap


class TeamError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Host claim - one person can claim host for a chat's team-game setup, which
# gives them add/delete rights over BOTH Team A and Team B (on top of each
# team's own captain).
# --------------------------------------------------------------------------- #
async def get_host(session: AsyncSession, chat_id: int) -> TeamHost | None:
    return await session.get(TeamHost, chat_id)


async def claim_host(session: AsyncSession, chat_id: int, user_id: int, user_name: str) -> TeamHost:
    existing = await get_host(session, chat_id)
    if existing is not None:
        raise TeamError(
            f"{existing.host_name or 'Someone'} is already the host for this chat's teams. "
            "A group admin or the bot owner can transfer host with /claimhost."
        )
    host = TeamHost(chat_id=chat_id, host_id=user_id, host_name=user_name)
    session.add(host)
    await session.flush()
    return host


async def force_claim_host(session: AsyncSession, chat_id: int, user_id: int, user_name: str) -> TeamHost:
    """Used by a group admin/owner to take over or reassign the host claim."""
    existing = await get_host(session, chat_id)
    if existing is not None:
        existing.host_id = user_id
        existing.host_name = user_name
        await session.flush()
        return existing
    return await claim_host(session, chat_id, user_id, user_name)


async def release_host(session: AsyncSession, chat_id: int) -> bool:
    existing = await get_host(session, chat_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True


async def is_host(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    host = await get_host(session, chat_id)
    return host is not None and host.host_id == user_id


async def get_team_by_slot(session: AsyncSession, chat_id: int, slot: str) -> Team | None:
    result = await session.execute(select(Team).where(Team.chat_id == chat_id, Team.slot == slot))
    return result.scalars().first()


async def get_match_teams(session: AsyncSession, chat_id: int) -> tuple[Team | None, Team | None]:
    """Returns (Team A, Team B) for this chat - either may be None if not created yet."""
    return (
        await get_team_by_slot(session, chat_id, "A"),
        await get_team_by_slot(session, chat_id, "B"),
    )


async def _user_already_on_a_team(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    all_teams = await list_teams(session, chat_id)
    for t in all_teams:
        members = await get_team_players(session, t.id)
        if any(m.user_id == user_id for m in members):
            return True
    return False


async def create_team(
    session: AsyncSession, chat_id: int, name: str, captain_id: int, captain_name: str, slot: str
) -> Team:
    if slot not in CAP_BY_SLOT:
        raise TeamError("Invalid team slot.")
    if await get_team_by_slot(session, chat_id, slot) is not None:
        raise TeamError(f"Team {slot} already exists in this chat.")
    # CRITICAL: a player can only ever be on ONE of the two teams in a chat.
    # Without this check, someone who captains Team A and then also creates
    # Team B ends up on both rosters - which crashes the match launch later
    # (the same user_id gets inserted twice into the same innings' Game).
    if await _user_already_on_a_team(session, chat_id, captain_id):
        raise TeamError("You're already on a team in this chat - you can't create/captain a second one.")
    existing = await session.execute(
        select(Team).where(Team.chat_id == chat_id, Team.name.ilike(name))
    )
    if existing.scalars().first() is not None:
        raise TeamError(f"A team named '{name}' already exists in this chat.")
    cap_color = CAP_BY_SLOT[slot]
    team = Team(chat_id=chat_id, name=name, captain_id=captain_id, slot=slot, cap_color=cap_color)
    session.add(team)
    await session.flush()
    session.add(
        TeamPlayer(team_id=team.id, user_id=captain_id, display_name=captain_name, batting_order=0)
    )
    await session.flush()
    return team


async def list_teams(session: AsyncSession, chat_id: int) -> list[Team]:
    result = await session.execute(select(Team).where(Team.chat_id == chat_id).order_by(Team.id))
    return list(result.scalars().all())


async def get_active_match(session: AsyncSession, chat_id: int) -> TeamMatch | None:
    result = await session.execute(
        select(TeamMatch)
        .where(TeamMatch.chat_id == chat_id, TeamMatch.status != MatchStatus.COMPLETED)
        .order_by(TeamMatch.id.desc())
    )
    return result.scalars().first()


async def delete_team(session: AsyncSession, team: Team) -> None:
    """Deletes a single team (and its roster, via cascade)."""
    await session.delete(team)
    await session.flush()


async def reset_teams(session: AsyncSession, chat_id: int) -> int:
    """Deletes every Team (and its TeamPlayer rows, via cascade) for this
    chat. Used by /resetteams to clear out bad/stale team data."""
    teams = await list_teams(session, chat_id)
    for t in teams:
        await session.delete(t)
    await session.flush()
    return len(teams)


async def get_team_players(session: AsyncSession, team_id: int) -> list[TeamPlayer]:
    result = await session.execute(
        select(TeamPlayer).where(TeamPlayer.team_id == team_id).order_by(TeamPlayer.batting_order)
    )
    return list(result.scalars().all())


async def join_team(session: AsyncSession, team: Team, user_id: int, display_name: str) -> TeamPlayer:
    if await _user_already_on_a_team(session, team.chat_id, user_id):
        raise TeamError("You're already on a team in this chat.")
    members = await get_team_players(session, team.id)
    if len(members) >= MAX_TEAM_SIZE:
        raise TeamError(f"🏏 {team.name} is already full ({MAX_TEAM_SIZE}/{MAX_TEAM_SIZE} players).")
    player = TeamPlayer(team_id=team.id, user_id=user_id, display_name=display_name, batting_order=len(members))
    session.add(player)
    await session.flush()
    return player


async def remove_player(session: AsyncSession, team: Team, captain_id: int, target_user_id: int) -> None:
    if team.captain_id != captain_id:
        raise TeamError("Only the team captain can remove players.")
    if target_user_id == team.captain_id:
        raise TeamError("The captain can't remove themselves.")
    members = await get_team_players(session, team.id)
    target = next((m for m in members if m.user_id == target_user_id), None)
    if target is None:
        raise TeamError("That player isn't on this team.")
    await session.delete(target)
    await session.flush()


async def set_batting_order(session: AsyncSession, team: Team, captain_id: int, ordered_user_ids: list[int]) -> None:
    if team.captain_id != captain_id:
        raise TeamError("Only the team captain can set the batting order.")
    members = {m.user_id: m for m in await get_team_players(session, team.id)}
    for idx, uid in enumerate(ordered_user_ids):
        if uid in members:
            members[uid].batting_order = idx
    await session.flush()


async def create_match(session: AsyncSession, chat_id: int, team_a: Team, team_b: Team, balls_per_over: int) -> TeamMatch:
    if team_a.id == team_b.id:
        raise TeamError("Pick two different teams.")
    for team in (team_a, team_b):
        members = await get_team_players(session, team.id)
        if len(members) < 1:
            raise TeamError(f"Team '{team.name}' has no players yet.")
    match = TeamMatch(
        chat_id=chat_id, team_a_id=team_a.id, team_b_id=team_b.id, balls_per_over=balls_per_over
    )
    session.add(match)
    await session.flush()
    return match


async def _populate_innings_roster(
    session: AsyncSession, game: Game, batting_team_id: int, bowling_team_id: int
) -> None:
    batters = await get_team_players(session, batting_team_id)
    bowlers = await get_team_players(session, bowling_team_id)
    # Defensive de-dupe: if a player somehow ended up on both rosters (e.g.
    # leftover bad data from before the create_team/join_team "one team per
    # chat" check existed), skip the duplicate instead of crashing the whole
    # match with a UNIQUE constraint error - the batting side always wins
    # the slot since they're inserted first.
    seen_user_ids: set[int] = set()
    for p in batters:
        if p.user_id in seen_user_ids:
            continue
        seen_user_ids.add(p.user_id)
        session.add(
            GamePlayer(
                game_id=game.id,
                user_id=p.user_id,
                display_name=p.display_name,
                batting_order=p.batting_order,
                is_bowler_pool=False,
            )
        )
    for i, p in enumerate(bowlers):
        if p.user_id in seen_user_ids:
            continue
        seen_user_ids.add(p.user_id)
        session.add(
            GamePlayer(
                game_id=game.id,
                user_id=p.user_id,
                display_name=p.display_name,
                batting_order=9000 + i,
                has_batted=True,  # sentinel: never eligible to bat this innings
                is_bowler_pool=True,
            )
        )
    await session.flush()


async def start_innings(
    session: AsyncSession, match: TeamMatch, batting_team_id: int, bowling_team_id: int
) -> tuple[Game, GamePlayer, GamePlayer]:
    game = Game(
        chat_id=match.chat_id,
        game_type=GameType.TEAM,
        status=GameStatus.QUEUE,
        balls_per_over=match.balls_per_over,
        created_by=0,
        team_match_id=match.id,
    )
    session.add(game)
    await session.flush()
    await _populate_innings_roster(session, game, batting_team_id, bowling_team_id)
    batter, bowler = await engine.start_game(session, game)
    match.status = MatchStatus.IN_PROGRESS
    await session.flush()
    return game, batter, bowler


async def innings_total(session: AsyncSession, game_id: int) -> int:
    players = await engine.get_players(session, game_id)
    return sum(p.runs for p in players if not p.is_bowler_pool)


async def get_team(session: AsyncSession, team_id: int) -> Team | None:
    return await session.get(Team, team_id)


async def get_match(session: AsyncSession, match_id: int) -> TeamMatch | None:
    return await session.get(TeamMatch, match_id)
