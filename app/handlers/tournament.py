"""Tournament handlers: registration, bracket start, and match chaining."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, filters

from app.database.database import get_session
from app.database.models import Game, MatchStatus, Tournament, TournamentMatch, TournamentStatus, User
from app.game import tournament as t_engine
from app.game.render import status_text
from app.keyboards.game import number_choice_keyboard, status_message_keyboard
from app.keyboards.admin import tournament_join_keyboard, tournament_menu_keyboard
from app.services.leaderboard import apply_tournament_win
from app.utils.helpers import display_name
from app.utils.permissions import is_authorized_controller

logger = logging.getLogger("cricbeast.tournament")


async def tournament_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏆 <b>TOURNAMENT</b>", parse_mode="HTML", reply_markup=tournament_menu_keyboard()
    )


async def tournament_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html("🏆 <b>TOURNAMENT</b>", reply_markup=tournament_menu_keyboard())


# --------------------------------------------------------------------------- #
# Create (name -> max participants -> spell, via simple text prompts)
# --------------------------------------------------------------------------- #
async def create_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_tourney_name"] = update.effective_chat.id
    await query.edit_message_text(
        "➕ <b>Create Tournament</b>\n\n"
        "Send: <code>Name, MaxPlayers, BallsPerOver</code>\n"
        "Example: <code>CricBeast Championship, 8, 6</code>",
        parse_mode="HTML",
    )


async def tourney_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_tourney_name") != update.effective_chat.id:
        return
    context.user_data.pop("awaiting_tourney_name", None)
    parts = [p.strip() for p in update.effective_message.text.split(",")]
    if len(parts) != 3:
        await update.effective_message.reply_text("⚠️ Format: Name, MaxPlayers, BallsPerOver")
        return
    name, max_raw, balls_raw = parts
    try:
        max_participants = int(max_raw)
        balls_per_over = int(balls_raw)
    except ValueError:
        await update.effective_message.reply_text("⚠️ MaxPlayers and BallsPerOver must be numbers.")
        return
    user = update.effective_user
    async with get_session() as session:
        try:
            t = await t_engine.create_tournament(
                session, update.effective_chat.id, name[:128], user.id, max_participants, balls_per_over
            )
        except t_engine.TournamentError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
    await update.effective_message.reply_html(
        f"🏆 Tournament <b>{name}</b> created!\nMax players: {max_participants}\n"
        "Players can now 🚪 Join Tournament."
    )


tourney_name_filter = filters.TEXT & ~filters.COMMAND


# --------------------------------------------------------------------------- #
# Browse / Join / Mine
# --------------------------------------------------------------------------- #
async def browse_tournaments_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        tournaments = await t_engine.list_open_tournaments(session, update.effective_chat.id)
    await query.answer()
    if not tournaments:
        await query.edit_message_text("No open tournaments right now.", reply_markup=tournament_menu_keyboard())
        return
    lines = [
        f"🏆 <b>{t.name}</b> — {len(t.participants or [])}/{t.max_participants} players"
        for t in tournaments
    ]
    await query.edit_message_text(
        "🔎 <b>Open Tournaments</b>\n\n" + "\n".join(lines), parse_mode="HTML", reply_markup=tournament_menu_keyboard()
    )


async def join_tournament_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with get_session() as session:
        tournaments = await t_engine.list_open_tournaments(session, update.effective_chat.id)
    await query.answer()
    if not tournaments:
        await query.edit_message_text("No open tournaments to join.", reply_markup=tournament_menu_keyboard())
        return
    await query.edit_message_text(
        "🚪 <b>Join Tournament</b>",
        reply_markup=tournament_join_keyboard([(t.id, t.name) for t in tournaments]),
    )


async def join_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tournament_id = int(query.data.split(":")[2])
    user = update.effective_user
    async with get_session() as session:
        t = await t_engine.get_tournament(session, tournament_id)
        if t is None:
            await query.answer("Tournament not found.", show_alert=True)
            return
        try:
            await t_engine.join_tournament(session, t, user.id, display_name(user))
        except t_engine.TournamentError as exc:
            await query.answer(str(exc), show_alert=True)
            return
    await query.answer("✅ Registered!")
    await query.edit_message_text(f"✅ You joined 🏆 <b>{t.name}</b>!", parse_mode="HTML")


async def my_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    async with get_session() as session:
        t = await t_engine.find_active_tournament_for_user(session, update.effective_chat.id, user.id)
    await query.answer()
    if t is None:
        await query.edit_message_text("You're not registered in any active tournament here.", reply_markup=tournament_menu_keyboard())
        return
    names = ", ".join(p["name"] for p in (t.participants or [])) or "—"
    await query.edit_message_text(
        f"📋 <b>{t.name}</b>\nStatus: {t.status.value}\nPlayers: {len(t.participants or [])}/{t.max_participants}\n{names}",
        parse_mode="HTML",
        reply_markup=tournament_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Start tournament
# --------------------------------------------------------------------------- #
async def start_tournament_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    async with get_session() as session:
        tournaments = await t_engine.list_open_tournaments(session, update.effective_chat.id)
        startable = [t for t in tournaments if t.creator_id == user.id or await is_authorized_controller(update, context, user.id, t.creator_id)]
        await query.answer()
        if not startable:
            await query.edit_message_text("No tournament you can start here.", reply_markup=tournament_menu_keyboard())
            return
        t = startable[0]
        try:
            await t_engine.start_tournament(session, t)
        except t_engine.TournamentError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.edit_message_text(f"▶️ Tournament <b>{t.name}</b> has started! Bracket generated.", parse_mode="HTML")
        await _kick_off_next_match(context, session, t)


async def _kick_off_next_match(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, t: Tournament) -> None:
    m = await t_engine.next_playable_match(session, t.id)
    if m is None:
        # Everything either finished or waiting on an earlier round.
        return
    a_name = await _name_of(session, m.player_a_id)
    b_name = await _name_of(session, m.player_b_id)
    try:
        await context.bot.send_message(
            chat_id=t.chat_id,
            text=f"🥊 <b>{m.round_name}</b>\n\n{a_name} 🆚 {b_name}\n\n{a_name} bats first!",
            parse_mode="HTML",
        )
    except TelegramError:
        pass
    game, batter, bowler = await t_engine.start_match_innings(
        session, m, t.chat_id, t.balls_per_over, m.player_a_id, a_name, m.player_b_id, b_name
    )
    await _send_innings_status(context, game, batter, bowler)
    from app.handlers.solo import _prompt_bowler_dm

    await _prompt_bowler_dm(context, session, game, batter, bowler)


async def _send_innings_status(context: ContextTypes.DEFAULT_TYPE, game: Game, batter, bowler) -> None:
    bot_username = (await context.bot.get_me()).username
    kb_rows = number_choice_keyboard("bat", game.id).inline_keyboard + status_message_keyboard(bot_username).inline_keyboard
    text = status_text(game, batter, bowler, [], waiting_on_dm=True)
    msg = await context.bot.send_message(
        chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows)
    )
    game.status_message_id = msg.message_id


async def _name_of(session: AsyncSession, user_id: int | None) -> str:
    if user_id is None:
        return "BYE"
    user = await session.get(User, user_id)
    return user.display_name if user else str(user_id)


# --------------------------------------------------------------------------- #
# Innings-over handling: called from handlers/solo.py for TOURNAMENT games
# --------------------------------------------------------------------------- #
async def handle_tournament_innings_over(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, resolution) -> None:
    tmatch: TournamentMatch = await session.get(TournamentMatch, game.tournament_match_id)
    t: Tournament = await session.get(Tournament, tmatch.tournament_id)

    result = await session.execute(select(Game).where(Game.tournament_match_id == tmatch.id))
    innings_played = [g for g in result.scalars().all() if g.status.value == "completed"]

    from app.game import engine as core_engine

    this_innings_players = await core_engine.get_players(session, game.id)
    batter_row = next(p for p in this_innings_players if not p.is_bowler_pool)
    bowler_row = next(p for p in this_innings_players if p.is_bowler_pool)

    if len(innings_played) == 1:
        # First innings done - second player now bats against the first.
        try:
            await context.bot.send_message(
                chat_id=game.chat_id,
                text=f"🏁 {batter_row.display_name} scored <b>{batter_row.runs}</b> runs.\n\nNow {bowler_row.display_name} bats!",
                parse_mode="HTML",
            )
        except TelegramError:
            pass
        game2, batter2, bowler2 = await t_engine.start_match_innings(
            session, tmatch, game.chat_id, game.balls_per_over,
            bowler_row.user_id, bowler_row.display_name, batter_row.user_id, batter_row.display_name,
        )
        await _send_innings_status(context, game2, batter2, bowler2)
        from app.handlers.solo import _prompt_bowler_dm

        await _prompt_bowler_dm(context, session, game2, batter2, bowler2)
        return

    # Both innings complete - determine the match winner.
    first_game = min(innings_played, key=lambda g: g.id)
    first_players = await core_engine.get_players(session, first_game.id)
    first_batter = next(p for p in first_players if not p.is_bowler_pool)

    score_a = first_batter.runs if first_batter.user_id == tmatch.player_a_id else batter_row.runs
    score_b = batter_row.runs if first_batter.user_id == tmatch.player_a_id else first_batter.runs
    winner_id = tmatch.player_a_id if score_a >= score_b else tmatch.player_b_id
    if score_a == score_b:
        winner_id = tmatch.player_a_id  # deterministic tiebreak: player A advances

    winner_name = await _name_of(session, winner_id)
    try:
        await context.bot.send_message(
            chat_id=game.chat_id,
            text=(
                f"🏁 <b>{tmatch.round_name} Result</b>\n\n"
                f"{await _name_of(session, tmatch.player_a_id)}: {score_a}\n"
                f"{await _name_of(session, tmatch.player_b_id)}: {score_b}\n\n"
                f"🏆 <b>{winner_name} advances!</b>"
            ),
            parse_mode="HTML",
        )
    except TelegramError:
        pass

    next_match = await t_engine.advance_winner(session, tmatch, winner_id)

    if next_match is None and tmatch.round_name == "Final":
        t.status = TournamentStatus.COMPLETED
        t.winner_id = winner_id
        await session.flush()
        await apply_tournament_win(session, winner_id, winner_name)
        try:
            await context.bot.send_message(
                chat_id=game.chat_id,
                text=f"🏅 <b>{winner_name} is the CricBeast Champion!</b> 🏆\n\nAdded to the Hall of Fame.",
                parse_mode="HTML",
            )
        except TelegramError:
            pass
        return

    await _kick_off_next_match(context, session, t)
