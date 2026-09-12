"""
Solo Game: spell selection, queue (/join, /leavesolo, /startsolo), and the
full ball-by-ball gameplay loop (group batting + DM bowling).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.database.database import get_session
from app.database.models import Game, GamePlayer, GameStatus, GameType
from app.game import engine
from app.game.render import (
    bowl_locked_text,
    bowler_dm_text,
    final_result_text,
    scoreboard_text,
    solo_queue_text,
    status_text,
    wicket_text,
)
from app.keyboards.game import (
    number_choice_keyboard,
    open_dm_keyboard,
    solo_queue_keyboard,
    spell_choice_keyboard,
    status_message_keyboard,
)
from app.keyboards.main import main_menu_keyboard
from app.services import media
from app.services.dm import DMFailed, send_dm
from app.services.leaderboard import apply_match_result
from app.services.logger import log_ball_to_channel
from app.utils.helpers import display_name
from app.utils.permissions import is_authorized_controller
from app.utils.rate_limit import debounce_guard, rate_limiter

logger = logging.getLogger("cricbeast.solo")


# --------------------------------------------------------------------------- #
# Menu / spell selection
# --------------------------------------------------------------------------- #
async def solo_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚖️ <b>Choose Spell</b>\n\nHow many balls per turn/over?",
        parse_mode="HTML",
        reply_markup=spell_choice_keyboard(),
    )


async def spell_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    balls_per_over = int(query.data.split(":")[2])
    chat = update.effective_chat
    user = update.effective_user

    async with get_session() as session:
        try:
            game = await engine.create_solo_game(session, chat.id, user.id, balls_per_over)
        except engine.GameAlreadyStarted:
            await query.answer("A solo game is already active in this chat!", show_alert=True)
            return
        await query.answer()
        players = await engine.get_players(session, game.id)
        text = solo_queue_text(game, players)
        msg = await query.edit_message_text(text, parse_mode="HTML", reply_markup=solo_queue_keyboard(game.id))
        game.status_message_id = msg.message_id
        await session.flush()


# --------------------------------------------------------------------------- #
# Queue management (/join, /leavesolo, /startsolo + matching callbacks)
# --------------------------------------------------------------------------- #
async def _refresh_queue_message(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game) -> None:
    players = await engine.get_players(session, game.id)
    text = solo_queue_text(game, players)
    if game.status_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.status_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=solo_queue_keyboard(game.id),
            )
        except TelegramError:
            pass


async def _try_autostart(update: Update, context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game) -> bool:
    from app.config import settings

    players = await engine.get_players(session, game.id)
    if len(players) >= settings.solo_max_players:
        await _launch_game(update, context, session, game)
        return True
    return False


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    async with get_session() as session:
        game = await engine.get_active_game(session, chat.id, GameType.SOLO)
        if game is None:
            await update.effective_message.reply_text("No solo game queue is open here. Start one from 🏏 Solo Game.")
            return
        try:
            await engine.join_game(session, game, user.id, display_name(user))
        except engine.GameError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
        await update.effective_message.reply_text(f"✅ {display_name(user)} joined the queue!")
        await _refresh_queue_message(context, session, game)
        await _try_autostart(update, context, session, game)


async def leavesolo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    async with get_session() as session:
        game = await engine.get_active_game(session, chat.id, GameType.SOLO)
        if game is None:
            await update.effective_message.reply_text("No solo game queue is open here.")
            return
        try:
            await engine.leave_game(session, game, user.id)
        except engine.GameError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
        await update.effective_message.reply_text(f"🚪 {display_name(user)} left the queue.")
        await _refresh_queue_message(context, session, game)


async def startsolo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    async with get_session() as session:
        game = await engine.get_active_game(session, chat.id, GameType.SOLO)
        if game is None:
            await update.effective_message.reply_text("No solo game queue is open here.")
            return
        if game.status != GameStatus.QUEUE:
            await update.effective_message.reply_text("This game has already started.")
            return
        if not await is_authorized_controller(update, context, user.id, game.created_by):
            await update.effective_message.reply_text("⚠️ Only the game creator, a group admin, or the bot owner can force-start.")
            return
        players = await engine.get_players(session, game.id)
        if not engine.can_force_start(players):
            await update.effective_message.reply_text("⚠️ Need at least 2 players to start.")
            return
        await _launch_game(update, context, session, game)


async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not debounce_guard.allow(user.id, query.data):
        await query.answer()
        return
    game_id = int(query.data.split(":")[2])
    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None:
            await query.answer("Game not found.", show_alert=True)
            return
        try:
            await engine.join_game(session, game, user.id, display_name(user))
        except engine.GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("✅ Joined!")
        await _refresh_queue_message(context, session, game)
        await _try_autostart(update, context, session, game)


async def leave_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    game_id = int(query.data.split(":")[2])
    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None:
            await query.answer("Game not found.", show_alert=True)
            return
        try:
            await engine.leave_game(session, game, user.id)
        except engine.GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("🚪 Left the queue.")
        await _refresh_queue_message(context, session, game)


async def forcestart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    game_id = int(query.data.split(":")[2])
    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None or game.status != GameStatus.QUEUE:
            await query.answer("This game can't be force-started right now.", show_alert=True)
            return
        if not await is_authorized_controller(update, context, user.id, game.created_by):
            await query.answer("Only the creator, a group admin, or the owner can force-start.", show_alert=True)
            return
        players = await engine.get_players(session, game.id)
        if not engine.can_force_start(players):
            await query.answer("Need at least 2 players.", show_alert=True)
            return
        await query.answer("▶️ Starting!")
        await _launch_game(update, context, session, game)


async def cancelgame_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    game_id = int(query.data.split(":")[2])
    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None:
            await query.answer("Game not found.", show_alert=True)
            return
        if not await is_authorized_controller(update, context, user.id, game.created_by):
            await query.answer("Only the creator, a group admin, or the owner can cancel.", show_alert=True)
            return
        await engine.cancel_game(session, game)
        await query.answer("❌ Cancelled.")
        await query.edit_message_text("❌ Solo game cancelled.")


# --------------------------------------------------------------------------- #
# Game launch + bowler DM prompt
# --------------------------------------------------------------------------- #
async def _launch_game(update: Update, context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game) -> None:
    batter, bowler = await engine.start_game(session, game)
    await session.flush()
    balls = []
    text = status_text(game, batter, bowler, balls, waiting_on_dm=True)
    kb_rows = number_choice_keyboard("bat", game.id).inline_keyboard + status_message_keyboard(
        (await context.bot.get_me()).username
    ).inline_keyboard
    keyboard = InlineKeyboardMarkup(kb_rows)
    if game.status_message_id:
        try:
            msg = await context.bot.edit_message_text(
                chat_id=game.chat_id, message_id=game.status_message_id, text=text,
                parse_mode="HTML", reply_markup=keyboard,
            )
        except TelegramError:
            msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
    else:
        msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
    game.status_message_id = msg.message_id
    await session.flush()
    await _prompt_bowler_dm(context, session, game, batter, bowler)


async def _prompt_bowler_dm(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, batter: GamePlayer, bowler: GamePlayer) -> None:
    score_line = f"{batter.runs}/{sum(1 for p in await engine.get_players(session, game.id) if p.is_out)}"
    text = bowler_dm_text(game, batter, score_line)
    try:
        await send_dm(context, bowler.user_id, text, reply_markup=number_choice_keyboard("bowl", game.id))
    except DMFailed:
        bot_username = (await context.bot.get_me()).username
        try:
            await context.bot.send_message(
                chat_id=game.chat_id,
                text=(
                    f"⚠️ {bowler.display_name}, I couldn't DM you! "
                    "Please open a private chat with me and press Start, then tap Retry below."
                ),
                reply_markup=InlineKeyboardMarkup(
                    open_dm_keyboard(bot_username).inline_keyboard
                    + [[InlineKeyboardButton("🔁 Retry DM", callback_data=f"bowl:retry:{game.id}")]]
                ),
            )
        except TelegramError:
            pass


async def bowl_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    game_id = int(query.data.split(":")[2])
    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None or game.status != GameStatus.IN_PROGRESS or game.current_bowler_id != user.id:
            await query.answer("This retry isn't for you.", show_alert=True)
            return
        players = await engine.get_players(session, game.id)
        by_id = {p.user_id: p for p in players}
        batter = by_id[game.current_batter_id]
        bowler = by_id[game.current_bowler_id]
        await query.answer("Retrying DM...")
        await _prompt_bowler_dm(context, session, game, batter, bowler)


# --------------------------------------------------------------------------- #
# Gameplay: number selection callbacks
# --------------------------------------------------------------------------- #
async def bat_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not rate_limiter.allow(user.id):
        await query.answer("Slow down!", show_alert=False)
        return
    if not debounce_guard.allow(user.id, query.data):
        await query.answer()
        return
    _, game_id_raw, number_raw = query.data.split(":")
    game_id, number = int(game_id_raw), int(number_raw)

    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None:
            await query.answer("Game not found.", show_alert=True)
            return
        try:
            resolution = await engine.submit_batter_number(session, game, user.id, number)
        except engine.GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer(f"🏏 You chose {number}!")
        await _after_submission(context, session, game, resolution)


async def bowl_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not rate_limiter.allow(user.id):
        await query.answer("Slow down!", show_alert=False)
        return
    if not debounce_guard.allow(user.id, query.data):
        await query.answer()
        return
    _, game_id_raw, number_raw = query.data.split(":")
    game_id, number = int(game_id_raw), int(number_raw)

    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None:
            await query.answer("Game not found.", show_alert=True)
            return
        try:
            resolution = await engine.submit_bowler_number(session, game, user.id, number)
        except engine.GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.edit_message_text(bowl_locked_text())
        await query.answer()
        await _after_submission(context, session, game, resolution)


# --------------------------------------------------------------------------- #
# Post-ball resolution: update group, scoreboards, next prompts, game end
# --------------------------------------------------------------------------- #
async def _after_submission(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, resolution) -> None:
    if resolution is None:
        return  # waiting on the other player, nothing to render yet

    from app.database.models import Ball as BallModel
    from sqlalchemy import select as sa_select

    result = await session.execute(sa_select(BallModel).where(BallModel.game_id == game.id).order_by(BallModel.id))
    all_balls = list(result.scalars().all())

    await log_ball_to_channel(
        context,
        game,
        resolution.ball,
        resolution.batter.display_name,
        resolution.bowler.display_name,
        score_after=resolution.batter.runs,
        wickets_after=sum(1 for p in resolution.all_players if p.is_out),
    )

    if resolution.is_wicket:
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=wicket_text(resolution.batter), parse_mode="HTML")
        except TelegramError:
            pass
        await media.send_event_media(
            _bound_send_video(context, game.chat_id), media.OUT
        )
    elif resolution.ball.runs == 4:
        await media.send_event_media(_bound_send_video(context, game.chat_id), media.FOUR)
    elif resolution.ball.runs == 6:
        await media.send_event_media(_bound_send_video(context, game.chat_id), media.SIX)

    if resolution.is_over_complete and not resolution.is_game_over:
        over_balls = [
            ("W" if b.result.value == "out" else b.runs)
            for b in all_balls
            if b.over_number == game.current_over - 1
        ]
        board_batter = resolution.next_batter or resolution.batter
        try:
            await context.bot.send_message(
                chat_id=game.chat_id,
                text=scoreboard_text(game, board_batter, resolution.bowler, over_balls),
                parse_mode="HTML",
            )
        except TelegramError:
            pass

    if resolution.is_game_over:
        if game.game_type == GameType.TEAM:
            from app.handlers.team import handle_team_innings_over

            await handle_team_innings_over(context, session, game, resolution)
        elif game.game_type == GameType.TOURNAMENT:
            from app.handlers.tournament import handle_tournament_innings_over

            await handle_tournament_innings_over(context, session, game, resolution)
        else:
            await _finish_game(context, session, game, resolution)
        return

    # Game continues: figure out current batter/bowler and re-render status.
    players = await engine.get_players(session, game.id)
    by_id = {p.user_id: p for p in players}
    batter = by_id[game.current_batter_id]
    bowler = by_id[game.current_bowler_id]

    bowler_changed = resolution.next_bowler is not None
    batter_changed = resolution.next_batter is not None

    text = status_text(game, batter, bowler, all_balls, waiting_on_dm=True)
    bot_username = (await context.bot.get_me()).username
    kb_rows = number_choice_keyboard("bat", game.id).inline_keyboard + status_message_keyboard(bot_username).inline_keyboard
    keyboard = InlineKeyboardMarkup(kb_rows)
    if game.status_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=game.chat_id, message_id=game.status_message_id, text=text,
                parse_mode="HTML", reply_markup=keyboard,
            )
        except TelegramError:
            msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
            game.status_message_id = msg.message_id
    else:
        msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        game.status_message_id = msg.message_id

    if bowler_changed or batter_changed:
        await _prompt_bowler_dm(context, session, game, batter, bowler)

    await session.flush()


def _bound_send_video(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    import functools
    return functools.partial(context.bot.send_video, chat_id=chat_id)


async def _finish_game(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, resolution) -> None:
    players = resolution.all_players
    text = final_result_text(game, players, resolution.winner)
    try:
        if game.status_message_id:
            await context.bot.edit_message_text(
                chat_id=game.chat_id, message_id=game.status_message_id, text=text, parse_mode="HTML"
            )
        else:
            await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML")
    except TelegramError:
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML")
        except TelegramError:
            pass

    winner_id = resolution.winner.user_id if resolution.winner else None
    for p in players:
        await apply_match_result(
            session,
            user_id=p.user_id,
            display_name=p.display_name,
            runs_scored=p.runs,
            wickets_taken=p.wickets_taken,
            won=(p.user_id == winner_id),
            highest_score_candidate=p.runs,
        )
    await session.flush()
