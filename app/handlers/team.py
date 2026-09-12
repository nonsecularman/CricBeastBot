"""
Team Game handlers: create/join teams, list rosters, start a match, and the
innings-chaining glue that turns two solo-engine innings into one team match.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import ContextTypes, filters

from app.database.database import get_session
from app.database.models import MatchStatus, TeamMatch
from app.game import engine, team as team_engine
from app.handlers.solo import _announce_game_start
from app.keyboards.game import spell_choice_keyboard
from app.keyboards.team import team_join_keyboard, team_menu_keyboard, team_start_match_keyboard
from app.services.leaderboard import apply_match_result
from app.utils.helpers import display_name

logger = logging.getLogger("cricbeast.team")


# --------------------------------------------------------------------------- #
# Menu
# --------------------------------------------------------------------------- #
async def team_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👥 <b>TEAM GAME</b>\n\nManage teams and start a match.",
        parse_mode="HTML",
        reply_markup=team_menu_keyboard(),
    )


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "👥 <b>TEAM GAME</b>\n\nManage teams and start a match.", reply_markup=team_menu_keyboard()
    )


# --------------------------------------------------------------------------- #
# Create team (two-step: button -> ask name -> next text message creates it)
# --------------------------------------------------------------------------- #
async def create_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_team_name"] = update.effective_chat.id
    await query.edit_message_text(
        "➕ <b>Create Team</b>\n\nSend the team name as a message (e.g. <code>Titans</code>).",
        parse_mode="HTML",
    )


async def team_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting_chat = context.user_data.get("awaiting_team_name")
    if awaiting_chat != update.effective_chat.id:
        return
    context.user_data.pop("awaiting_team_name", None)
    name = update.effective_message.text.strip()[:64]
    user = update.effective_user
    async with get_session() as session:
        try:
            team = await team_engine.create_team(session, update.effective_chat.id, name, user.id, display_name(user))
        except team_engine.TeamError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
    await update.effective_message.reply_html(
        f"🏏 Team <b>{name}</b> created! You're the 👑 captain.\nOthers can join with 🚪 Join Team."
    )


team_name_filter = filters.TEXT & ~filters.COMMAND


# --------------------------------------------------------------------------- #
# Join team
# --------------------------------------------------------------------------- #
async def join_team_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        teams = await team_engine.list_teams(session, update.effective_chat.id)
    await query.answer()
    if not teams:
        await query.edit_message_text("No teams yet - create one first!", reply_markup=team_menu_keyboard())
        return
    await query.edit_message_text(
        "🚪 <b>Join Team</b>\n\nPick a team:",
        parse_mode="HTML",
        reply_markup=team_join_keyboard([(t.id, t.name) for t in teams]),
    )


async def join_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    team_id = int(query.data.split(":")[2])
    user = update.effective_user
    async with get_session() as session:
        team = await team_engine.get_team(session, team_id)
        if team is None:
            await query.answer("Team not found.", show_alert=True)
            return
        try:
            await team_engine.join_team(session, team, user.id, display_name(user))
        except team_engine.TeamError as exc:
            await query.answer(str(exc), show_alert=True)
            return
    await query.answer("✅ Joined the team!")
    await query.edit_message_text(f"✅ You joined 🏏 <b>{team.name}</b>!", parse_mode="HTML")


# --------------------------------------------------------------------------- #
# Team list
# --------------------------------------------------------------------------- #
async def team_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        teams = await team_engine.list_teams(session, update.effective_chat.id)
        blocks = []
        for t in teams:
            members = await team_engine.get_team_players(session, t.id)
            roster = "\n".join(
                f"• {m.display_name}" + (" 👑" if m.user_id == t.captain_id else "") for m in members
            )
            blocks.append(f"🏏 <b>{t.name}</b>\n{roster or '  (empty)'}")
    await query.answer()
    text = "📋 <b>Team List</b>\n\n" + ("\n\n".join(blocks) if blocks else "No teams yet.")
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=team_menu_keyboard())


# --------------------------------------------------------------------------- #
# Start match: pick team A -> pick team B -> pick spell -> launch innings 1
# --------------------------------------------------------------------------- #
async def start_match_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        teams = await team_engine.list_teams(session, update.effective_chat.id)
    await query.answer()
    if len(teams) < 2:
        await query.edit_message_text("Need at least 2 teams to start a match.", reply_markup=team_menu_keyboard())
        return
    context.user_data.pop("match_team_a", None)
    await query.edit_message_text(
        "⚔️ <b>Start Match</b>\n\n🅰️ Pick Team A:",
        parse_mode="HTML",
        reply_markup=team_start_match_keyboard([(t.id, t.name) for t in teams], slot_emoji="🅰️"),
    )


async def pick_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    team_id = int(query.data.split(":")[2])
    if "match_team_a" not in context.user_data:
        context.user_data["match_team_a"] = team_id
        async with get_session() as session:
            teams = await team_engine.list_teams(session, update.effective_chat.id)
        remaining = [(t.id, t.name) for t in teams if t.id != team_id]
        await query.answer()
        await query.edit_message_text(
            "🅱️ Pick Team B:",
            parse_mode="HTML",
            reply_markup=team_start_match_keyboard(remaining, slot_emoji="🅱️"),
        )
        return

    team_a_id = context.user_data.pop("match_team_a")
    team_b_id = team_id
    context.user_data["match_team_b"] = team_b_id
    context.user_data["match_team_a_final"] = team_a_id
    async with get_session() as session:
        team_a = await team_engine.get_team(session, team_a_id)
        team_b = await team_engine.get_team(session, team_b_id)
    await query.answer()
    await query.edit_message_text(
        f"🅰️ {team_a.name} 🆚 🅱️ {team_b.name}\n\n⚖️ Choose Spell (balls per over):",
        parse_mode="HTML",
        reply_markup=spell_choice_keyboard(),
    )
    context.user_data["awaiting_match_spell"] = True


async def match_spell_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercepts solo:spell:N callbacks when they're actually meant to launch a team match."""
    query = update.callback_query
    balls_per_over = int(query.data.split(":")[2])
    team_a_id = context.user_data.pop("match_team_a_final", None)
    team_b_id = context.user_data.pop("match_team_b", None)
    context.user_data.pop("awaiting_match_spell", None)
    if team_a_id is None or team_b_id is None:
        # Not a team-match flow - let the solo handler deal with it.
        from app.handlers.solo import spell_choice_callback as solo_spell_choice_callback

        await solo_spell_choice_callback(update, context)
        return

    async with get_session() as session:
        team_a = await team_engine.get_team(session, team_a_id)
        team_b = await team_engine.get_team(session, team_b_id)
        try:
            match = await team_engine.create_match(
                session, update.effective_chat.id, team_a, team_b, balls_per_over
            )
            game, batter, bowler = await team_engine.start_innings(session, match, team_a.id, team_b.id)
        except (team_engine.TeamError, engine.GameError) as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("⚔️ Match started!")
        intro = (
            f"⚔️ <b>{team_a.name} 🆚 {team_b.name}</b>\n"
            f"🏏 {team_a.name} batting first!\n\n"
        )
        await _announce_game_start(context, session, game, batter, bowler, intro_text=intro)


# --------------------------------------------------------------------------- #
# Innings-over handling: called from handlers/solo.py when a TEAM game ends
# --------------------------------------------------------------------------- #
async def handle_team_innings_over(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game, resolution) -> None:
    match: TeamMatch = await session.get(TeamMatch, game.team_match_id)
    total = await team_engine.innings_total(session, game.id)

    if match.current_innings == 1:
        batting_team_id, bowling_team_id = await _innings_team_ids(session, game, match)
        if batting_team_id == match.team_a_id:
            match.team_a_score = total
        else:
            match.team_b_score = total
        match.current_innings = 2
        await session.commit()

        team_a = await team_engine.get_team(session, match.team_a_id)
        team_b = await team_engine.get_team(session, match.team_b_id)
        first_innings_team_name = team_a.name if batting_team_id == match.team_a_id else team_b.name
        second_innings_team_name = team_b.name if batting_team_id == match.team_a_id else team_a.name
        try:
            await context.bot.send_message(
                chat_id=game.chat_id,
                text=(
                    f"🏁 <b>Innings Break</b>\n\n"
                    f"{first_innings_team_name} scored <b>{total}</b> runs.\n\n"
                    f"Now {second_innings_team_name} bats! Target: <b>{total + 1}</b> 🎯"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send innings-break message for match %s: %s", match.id, exc)

        game2, batter2, bowler2 = await team_engine.start_innings(session, match, bowling_team_id, batting_team_id)
        intro2 = f"⚔️ <b>{second_innings_team_name} chasing {total + 1}</b>\n\n"
        await _announce_game_start(context, session, game2, batter2, bowler2, intro_text=intro2)
        return

    # Second innings just finished - finalize the match.
    batting_team_id, _bowling_team_id = await _innings_team_ids(session, game, match)
    if batting_team_id == match.team_a_id:
        match.team_a_score = total
    else:
        match.team_b_score = total
    match.status = MatchStatus.COMPLETED
    winner_team_id = match.team_a_id if match.team_a_score >= match.team_b_score else match.team_b_id
    if match.team_a_score == match.team_b_score:
        winner_team_id = None
    match.winner_team_id = winner_team_id
    # Commit the match result FIRST, before any Telegram sends or stat
    # updates below - a notify-phase hiccup must never roll back a finished
    # match back to "in progress".
    await session.commit()

    team_a = await team_engine.get_team(session, match.team_a_id)
    team_b = await team_engine.get_team(session, match.team_b_id)
    winner_name = None
    if winner_team_id == match.team_a_id:
        winner_name = team_a.name
    elif winner_team_id == match.team_b_id:
        winner_name = team_b.name

    text = (
        "🏁 <b>MATCH OVER</b>\n\n"
        f"🏏 {team_a.name}: {match.team_a_score}\n"
        f"🏏 {team_b.name}: {match.team_b_score}\n\n"
        + (f"🏆 <b>{winner_name} wins!</b>" if winner_name else "🤝 It's a tie!")
    )
    try:
        await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send match-over message for match %s: %s", match.id, exc)

    for team_id, won in (
        (match.team_a_id, winner_team_id == match.team_a_id),
        (match.team_b_id, winner_team_id == match.team_b_id),
    ):
        members = await team_engine.get_team_players(session, team_id)
        for m in members:
            await apply_match_result(
                session,
                user_id=m.user_id,
                display_name=m.display_name,
                runs_scored=0,
                wickets_taken=0,
                won=won,
                highest_score_candidate=0,
            )
    await session.commit()


async def _innings_team_ids(session: AsyncSession, game, match: TeamMatch) -> tuple[int, int]:
    """Return (batting_team_id, bowling_team_id) for the just-finished innings `game`."""
    players = await engine.get_players(session, game.id)
    batters = {p.user_id for p in players if not p.is_bowler_pool}
    team_a_players = {p.user_id for p in await team_engine.get_team_players(session, match.team_a_id)}
    if batters & team_a_players:
        return match.team_a_id, match.team_b_id
    return match.team_b_id, match.team_a_id
