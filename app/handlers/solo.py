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
    ball_result_text,
    batter_turn_text,
    bowl_locked_text,
    bowler_dm_text,
    final_result_text,
    scoreboard_text,
    solo_queue_text,
    status_text,
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
            await query.answer(
                "A solo game is already active here! Send /cancel to stop it, then try again.",
                show_alert=True,
            )
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
        await session.commit()
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
        await session.commit()
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
        await session.commit()
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
        await session.commit()
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
        await session.commit()
        await query.answer("❌ Cancelled.")
        try:
            await query.edit_message_text("❌ Solo game cancelled.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to edit cancel confirmation for game %s: %s", game.id, exc)


# --------------------------------------------------------------------------- #
# Game launch + bowler DM prompt
# --------------------------------------------------------------------------- #
async def _launch_game(update: Update, context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game) -> None:
    batter, bowler = await engine.start_game(session, game)
    await _announce_game_start(context, session, game, batter, bowler)


async def _announce_game_start(
    context: ContextTypes.DEFAULT_TYPE,
    session: AsyncSession,
    game: Game,
    batter: GamePlayer,
    bowler: GamePlayer,
    intro_text: str = "",
) -> None:
    """
    Shared "first ball of a game" announcement - used by Solo (_launch_game)
    AND Team Game (each innings launch in app/handlers/team.py), so both game
    modes get the exact same reliable behaviour: commit-before-notify, the
    batter's fresh group ping, and the bowler's DM prompt.
    """
    # CRITICAL: commit the game-start transaction right now, BEFORE any
    # Telegram calls below. Everything in this function only reads what we
    # just committed, so any hiccup while sending messages (network blip,
    # rate limit, an unexpected exception) can never roll back the fact that
    # the game actually started - that was the root cause of "This game
    # isn't in progress" showing up right after a successful-looking launch.
    await session.commit()

    try:
        bot_username = (await context.bot.get_me()).username
    except Exception as exc:  # noqa: BLE001 - never let this crash the launch
        logger.warning("get_me() failed while launching game %s: %s", game.id, exc)
        bot_username = None

    balls = []
    text = intro_text + status_text(game, batter, bowler, balls, waiting_on_dm=True)
    keyboard = status_message_keyboard(bot_username) if bot_username else None

    try:
        if game.status_message_id:
            try:
                msg = await context.bot.edit_message_text(
                    chat_id=game.chat_id, message_id=game.status_message_id, text=text,
                    parse_mode="HTML", reply_markup=keyboard,
                )
            except Exception:
                msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        else:
            msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        game.status_message_id = msg.message_id
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to post/update status message for game %s: %s", game.id, exc)

    await _send_batter_turn_ping(context, game, batter)

    await _prompt_bowler_dm(context, session, game, batter, bowler)


async def _send_batter_turn_ping(context: ContextTypes.DEFAULT_TYPE, game: Game, batter: GamePlayer) -> None:
    """
    Pings the batter for their turn with a random GIF (rotates through
    whatever URLs are configured in MEDIA_BATTER_TURN - add more there
    anytime, no code changes needed) plus the caption. Falls back to a
    plain text message if no media is configured or the send fails, so the
    ping is never silently dropped.
    """
    caption = batter_turn_text(batter)
    sent = None
    try:
        sent = await media.send_event_media(
            _bound_send_video(context, game.chat_id), media.BATTER_TURN, caption=caption
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send batter-turn media for game %s: %s", game.id, exc)
    if sent is None:
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=caption, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send batter-turn ping for game %s: %s", game.id, exc)


async def _prompt_bowler_dm(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, batter: GamePlayer, bowler: GamePlayer) -> None:
    score_line = f"{batter.runs}/{sum(1 for p in await engine.get_players(session, game.id) if p.is_out)}"
    text = bowler_dm_text(game, batter, score_line)
    try:
        await send_dm(context, bowler.user_id, text, reply_markup=number_choice_keyboard("bowl", game.id))
    except DMFailed:
        try:
            bot_username = (await context.bot.get_me()).username
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
        except Exception as exc:  # noqa: BLE001 - never let a notification failure crash the game
            logger.warning("Failed to send 'couldn't DM you' fallback for game %s: %s", game.id, exc)
    except Exception as exc:  # noqa: BLE001 - any other unexpected error while DMing the bowler
        logger.warning("Unexpected error prompting bowler DM for game %s: %s", game.id, exc)


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


async def batter_text_number_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lets the batter just TYPE a number (1-6) in the group instead of tapping
    a button - this is the only way to bat now (works for Solo AND Team,
    since both share the exact same underlying game engine/Game row).
    Silently ignores the message if it's not actually that user's turn to
    bat, so random "1"-"6" texts in a busy group don't trigger noisy replies.
    """
    message = update.effective_message
    text = (message.text or "").strip()
    if text not in {"1", "2", "3", "4", "5", "6"}:
        return
    chat = update.effective_chat
    if chat is None or chat.type == "private":
        return
    user = update.effective_user
    if not rate_limiter.allow(user.id):
        return
    number = int(text)

    async with get_session() as session:
        game = await engine.get_game_awaiting_batter(session, chat.id, user.id)
        if game is None:
            return  # not this user's turn (or no active game) - ignore quietly
        try:
            resolution = await engine.submit_batter_number(session, game, user.id, number)
        except engine.GameError:
            return  # e.g. they already batted this ball - ignore quietly
        try:
            await message.reply_text(f"🏏 You chose {number}!")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to ack batter's typed number for game %s: %s", game.id, exc)
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
        await query.answer()
        try:
            await query.edit_message_text(bowl_locked_text())
        except Exception as exc:  # noqa: BLE001 - never let this roll back the submitted number
            logger.warning("Failed to edit bowl-locked DM message for game %s: %s", game.id, exc)
        await _after_submission(context, session, game, resolution)


# --------------------------------------------------------------------------- #
# Post-ball resolution: update group, scoreboards, next prompts, game end
# --------------------------------------------------------------------------- #
async def _after_submission(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, resolution) -> None:
    if resolution is None:
        return  # waiting on the other player, nothing to render yet

    from app.database.models import Ball as BallModel
    from sqlalchemy import select as sa_select

    # CRITICAL: commit the ball resolution right now, BEFORE any Telegram
    # calls below (GIF/media sends, DM prompts, scoreboard, etc). Everything
    # after this point only reads what we just committed, so a network
    # hiccup or unexpected error while notifying players can never roll back
    # a ball that already happened - that was the root cause of games
    # getting "confused"/stuck: the group would show ball 1's result, but an
    # error further down (e.g. a failed get_me() call) would unwind the
    # whole transaction and silently revert the game's state underneath it.
    await session.commit()

    result = await session.execute(sa_select(BallModel).where(BallModel.game_id == game.id).order_by(BallModel.id))
    all_balls = list(result.scalars().all())

    try:
        await log_ball_to_channel(
            context,
            game,
            resolution.ball,
            resolution.batter.display_name,
            resolution.bowler.display_name,
            score_after=resolution.batter.runs,
            wickets_after=sum(1 for p in resolution.all_players if p.is_out),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send game log for game %s: %s", game.id, exc)

    # Every ball (not just wickets/boundaries) gets its own GIF + revealed
    # numbers message, tagging both the batter and bowler - this is the
    # "ball delivered" beat shown after each resolved ball.
    ball_caption = ball_result_text(
        resolution.batter,
        resolution.bowler,
        resolution.ball.batter_number,
        resolution.ball.bowler_number,
        resolution.ball.runs,
        resolution.is_wicket,
    )
    if resolution.is_wicket:
        media_event = media.OUT
    elif resolution.ball.runs == 6:
        media_event = media.SIX
    elif resolution.ball.runs == 4:
        media_event = media.FOUR
    else:
        media_event = media.BATTING

    try:
        sent_media = await media.send_event_media(
            _bound_send_video(context, game.chat_id), media_event, caption=ball_caption
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send ball media for game %s: %s", game.id, exc)
        sent_media = None
    if sent_media is None:
        # No media configured / delivery failed - still send the text so the
        # ball result and tags are never silently dropped.
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=ball_caption, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send ball caption text for game %s: %s", game.id, exc)

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send scoreboard for game %s: %s", game.id, exc)

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

    text = status_text(game, batter, bowler, all_balls, waiting_on_dm=True)
    try:
        bot_username = (await context.bot.get_me()).username
    except Exception as exc:  # noqa: BLE001 - never let this crash ball resolution
        logger.warning("get_me() failed while rendering status for game %s: %s", game.id, exc)
        bot_username = None
    keyboard = status_message_keyboard(bot_username) if bot_username else None
    try:
        if game.status_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=game.chat_id, message_id=game.status_message_id, text=text,
                    parse_mode="HTML", reply_markup=keyboard,
                )
            except Exception:
                msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
                game.status_message_id = msg.message_id
                await session.commit()
        else:
            msg = await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
            game.status_message_id = msg.message_id
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to post/update status message for game %s: %s", game.id, exc)

    # IMPORTANT: also send the batter a brand-new, separate "your turn"
    # ping in the group EVERY ball - editing the status message above does
    # NOT trigger a Telegram notification even though it contains their tag,
    # so without this fresh message the batter never actually gets pinged.
    await _send_batter_turn_ping(context, game, batter)

    # IMPORTANT: prompt the bowler again for EVERY ball while the game is
    # still on - not only when the bowler/batter changes. Within the same
    # over the same bowler must submit a fresh number for each delivery, and
    # their previous DM message's keyboard was already replaced with the
    # "locked" text, so without this the game stalls after ball 1.
    await _prompt_bowler_dm(context, session, game, batter, bowler)

    await session.commit()


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
