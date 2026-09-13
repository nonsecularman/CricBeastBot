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
from app.keyboards.team import (
    team_create_choice_keyboard,
    team_delete_keyboard,
    team_join_keyboard,
    team_menu_keyboard,
)
from app.services.leaderboard import apply_match_result
from app.utils.helpers import display_name
from app.utils.permissions import is_admin_or_owner

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
# Create team: pick which slot to create (Team A = Blue Cap, Team B = Red
# Cap - fixed, chosen together in one tap), then send the team name.
# --------------------------------------------------------------------------- #
async def create_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id
    async with get_session() as session:
        team_a, team_b = await team_engine.get_match_teams(session, chat_id)
    await query.answer()
    if team_a is not None and team_b is not None:
        await query.edit_message_text(
            "⚠️ Team A and Team B already exist in this chat.\n"
            "Ask a group admin or the bot owner to delete a team first (🗑️ Delete Team).",
            reply_markup=team_menu_keyboard(),
        )
        return
    await query.edit_message_text(
        "➕ <b>Create Team</b>\n\nChoose which team to create:",
        parse_mode="HTML",
        reply_markup=team_create_choice_keyboard(team_a is not None, team_b is not None),
    )


async def create_team_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    slot = query.data.split(":")[2]  # "team:createslot:A" / "team:createslot:B"
    cap_color = team_engine.CAP_BY_SLOT[slot]
    context.user_data["creating_team_slot"] = slot
    context.user_data["awaiting_team_name"] = update.effective_chat.id
    await query.answer()
    cap_label = team_engine.CAP_LABELS[cap_color]
    await query.edit_message_text(
        f"{team_engine.CAP_EMOJI[cap_color]} Creating <b>Team {slot}</b> ({cap_label})\n\n"
        "Now send the team name as a message (e.g. Warriors, Thunder, Kings, etc.)",
        parse_mode="HTML",
    )


async def team_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting_chat = context.user_data.get("awaiting_team_name")
    if awaiting_chat != update.effective_chat.id:
        return
    slot = context.user_data.get("creating_team_slot")
    if slot is None:
        return  # stray text with no pending create-team flow - ignore
    context.user_data.pop("awaiting_team_name", None)
    context.user_data.pop("creating_team_slot", None)

    name = update.effective_message.text.strip()[:64]
    user = update.effective_user
    async with get_session() as session:
        try:
            team = await team_engine.create_team(
                session, update.effective_chat.id, name, user.id, display_name(user), slot
            )
        except team_engine.TeamError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
        await session.commit()
    cap_color = team_engine.CAP_BY_SLOT[slot]
    cap_label = team_engine.CAP_LABELS[cap_color]
    await update.effective_message.reply_html(
        f"{team_engine.CAP_EMOJI[cap_color]} <b>Team {team.slot} created!</b>\n\n"
        f"🧢 Cap: {cap_label}\n"
        f"🏏 Name: {team.name}\n"
        f"👑 Captain: {display_name(user)}\n\n"
        f"Others can join with 🚪 Join Team, or you can add latecomers yourself with "
        f"<code>/add_{team.slot.lower()}</code> (reply to their message with that command)."
    )


team_name_filter = filters.TEXT & ~filters.COMMAND


# --------------------------------------------------------------------------- #
# Join team
# --------------------------------------------------------------------------- #
async def join_team_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        team_a, team_b = await team_engine.get_match_teams(session, update.effective_chat.id)
    await query.answer()
    if team_a is None and team_b is None:
        await query.edit_message_text("No teams yet - create one first!", reply_markup=team_menu_keyboard())
        return
    await query.edit_message_text(
        "🚪 <b>Join Team</b>\n\nPick your team:",
        parse_mode="HTML",
        reply_markup=team_join_keyboard(team_a, team_b),
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
        await session.commit()
    cap_emoji = team_engine.CAP_EMOJI[team.cap_color]
    cap_label = team_engine.CAP_LABELS[team.cap_color]
    await query.answer("✅ Joined the team!")
    await query.edit_message_text(
        f"{cap_emoji} <b>You Joined TEAM {team.slot}</b>\n\n"
        f"🧢 Team Cap: {cap_label}\n"
        f"🏏 Team Name: {team.name}\n\n"
        "You're ready for the match! 🎉",
        parse_mode="HTML",
    )


# --------------------------------------------------------------------------- #
# Delete team
# --------------------------------------------------------------------------- #
async def delete_team_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        team_a, team_b = await team_engine.get_match_teams(session, update.effective_chat.id)
    await query.answer()
    if team_a is None and team_b is None:
        await query.edit_message_text("No teams to delete in this chat.", reply_markup=team_menu_keyboard())
        return
    await query.edit_message_text(
        "🗑️ <b>Delete Team</b>\n\nPick which team to delete (that team's captain, a group admin, or the bot owner only):",
        parse_mode="HTML",
        reply_markup=team_delete_keyboard(team_a, team_b),
    )


async def delete_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    team_id = int(query.data.split(":")[2])
    user = update.effective_user
    async with get_session() as session:
        team = await team_engine.get_team(session, team_id)
        if team is None:
            await query.answer("Team not found.", show_alert=True)
            return
        authorized = user.id == team.captain_id or await is_admin_or_owner(update, context, user.id)
        if not authorized:
            await query.answer(
                "Only that team's captain, a group admin, or the bot owner can delete it.", show_alert=True
            )
            return
        active_match = await team_engine.get_active_match(session, team.chat_id)
        if active_match is not None and team_id in (active_match.team_a_id, active_match.team_b_id):
            await query.answer(
                "Can't delete a team that's in an active match - finish the match first.", show_alert=True
            )
            return
        name, slot = team.name, team.slot
        await team_engine.delete_team(session, team)
        await session.commit()
    await query.answer("🗑️ Team deleted.")
    await query.edit_message_text(
        f"🗑️ <b>Team {slot} ({name}) has been deleted.</b>\nYou can create a new Team {slot} anytime.",
        parse_mode="HTML",
        reply_markup=team_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Manual add: /add_a and /add_b - reply to the target player's message
# --------------------------------------------------------------------------- #
async def _add_to_team_command(update: Update, context: ContextTypes.DEFAULT_TYPE, slot: str) -> None:
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text("⚠️ This only works in a group chat.")
        return
    reply = update.message.reply_to_message if update.message else None
    if reply is None or reply.from_user is None:
        await update.effective_message.reply_text(
            f"⚠️ Reply to the player's message with /add_{slot.lower()} to add them to Team {slot}."
        )
        return
    target = reply.from_user
    if target.is_bot:
        await update.effective_message.reply_text("⚠️ Can't add a bot to a team.")
        return

    requester = update.effective_user
    async with get_session() as session:
        team = await team_engine.get_team_by_slot(session, update.effective_chat.id, slot)
        if team is None:
            await update.effective_message.reply_text(
                f"⚠️ Team {slot} doesn't exist yet in this chat - create it first with ➕ Create Team."
            )
            return
        authorized = requester.id == team.captain_id or await is_admin_or_owner(update, context, requester.id)
        if not authorized:
            await update.effective_message.reply_text(
                "⚠️ Only that team's captain, a group admin, or the bot owner can add players."
            )
            return
        try:
            await team_engine.join_team(session, team, target.id, display_name(target))
        except team_engine.TeamError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
        await session.commit()
    cap_emoji = team_engine.CAP_EMOJI[team.cap_color]
    await update.effective_message.reply_html(
        f"✅ {display_name(target)} added to {cap_emoji} <b>Team {slot} — {team.name}</b>!"
    )


async def add_a_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_to_team_command(update, context, "A")


async def add_b_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_to_team_command(update, context, "B")


async def reset_teams_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes Team A and Team B (and their rosters) for this chat, so a new
    Create Team flow can start clean. Group-admin/owner only - this throws
    away the current teams' rosters."""
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text("⚠️ This only works in a group chat.")
        return
    user = update.effective_user
    if not await is_admin_or_owner(update, context, user.id):
        await update.effective_message.reply_text(
            "⚠️ Only a group admin or the bot owner can reset teams."
        )
        return
    async with get_session() as session:
        removed = await team_engine.reset_teams(session, update.effective_chat.id)
        await session.commit()
    if removed:
        await update.effective_message.reply_text(
            f"🔄 Cleared {removed} team(s) in this chat. You can create fresh Team A / Team B now."
        )
    else:
        await update.effective_message.reply_text("There were no teams to clear in this chat.")


# --------------------------------------------------------------------------- #
# Team list
# --------------------------------------------------------------------------- #
async def team_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        team_a, team_b = await team_engine.get_match_teams(session, update.effective_chat.id)
        blocks = []
        for label, t in (("A", team_a), ("B", team_b)):
            if t is None:
                blocks.append(f"Team {label}: <i>not created yet</i>")
                continue
            members = await team_engine.get_team_players(session, t.id)
            roster = "\n".join(
                f"• {m.display_name}" + (" 👑" if m.user_id == t.captain_id else "") for m in members
            )
            emoji = team_engine.CAP_EMOJI[t.cap_color]
            blocks.append(
                f"{emoji} <b>TEAM {label} — {t.name}</b> ({len(members)}/{team_engine.MAX_TEAM_SIZE})\n"
                f"{roster or '  (empty)'}"
            )
    await query.answer()
    text = "📋 <b>Team List</b>\n\n" + "\n\n".join(blocks)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=team_menu_keyboard())


# --------------------------------------------------------------------------- #
# Start match: Team A and Team B are already fixed per chat, so this goes
# straight to a confirmation + spell choice, then launches innings 1.
# --------------------------------------------------------------------------- #
async def start_match_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        team_a, team_b = await team_engine.get_match_teams(session, update.effective_chat.id)
    await query.answer()
    if team_a is None or team_b is None:
        await query.edit_message_text(
            "⚠️ Need both Team A and Team B created (with players) before starting a match.",
            reply_markup=team_menu_keyboard(),
        )
        return
    context.user_data["match_team_a_final"] = team_a.id
    context.user_data["match_team_b"] = team_b.id
    context.user_data["awaiting_match_spell"] = True
    cap_a = team_engine.CAP_EMOJI[team_a.cap_color]
    cap_b = team_engine.CAP_EMOJI[team_b.cap_color]
    await query.edit_message_text(
        f"⚔️ {cap_a} <b>TEAM A: {team_a.name}</b> 🆚 {cap_b} <b>TEAM B: {team_b.name}</b>\n\n"
        "⚖️ Choose Spell (balls per over):",
        parse_mode="HTML",
        reply_markup=spell_choice_keyboard(),
    )


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
