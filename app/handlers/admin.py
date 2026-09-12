"""
Owner / Admin control panel.

Every action here is gated by app.utils.permissions.is_owner (Telegram user
ID check, never username) and every use is written to the admin_logs table
via app.services.logger.record_admin_action.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, filters

from app.database.database import get_session
from app.database.models import Game, GameStatus
from app.game import engine
from app.game.render import final_result_text, status_text
from app.keyboards.admin import owner_panel_keyboard
from app.services.logger import record_admin_action
from app.utils.permissions import is_admin_or_owner, is_owner


def _owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_owner(user.id):
            target = update.callback_query or update.effective_message
            if update.callback_query:
                await update.callback_query.answer("⚠️ Owner only.", show_alert=True)
            else:
                await update.effective_message.reply_text("⚠️ This command is restricted to the bot owner.")
            return
        return await func(update, context)

    return wrapper


def _admin_only(func):
    """
    Group-admin tier (per the bot's command spec, /admin, /games and
    /stopgame are Admin-level, not Owner-only) - the bot owner always
    passes too.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not await is_admin_or_owner(update, context, user.id):
            await update.effective_message.reply_text(
                "⚠️ This command is restricted to group admins or the bot owner."
            )
            return
        return await func(update, context)

    return wrapper


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@_owner_only
async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "⚙️ <b>Owner Controls</b>\n\nActs on the most recent in-progress game in this chat.",
        reply_markup=owner_panel_keyboard(),
    )


cheat_command = owner_command


@_admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(Game).where(Game.chat_id == update.effective_chat.id).order_by(Game.id.desc()).limit(5)
        )
        games = list(result.scalars().all())
    if not games:
        await update.effective_message.reply_text("No games recorded in this chat yet.")
        return
    lines = [f"#{g.id} [{g.game_type.value}] {g.status.value}" for g in games]
    await update.effective_message.reply_text("🛠 Recent games:\n" + "\n".join(lines))


@_owner_only
async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Owner-only (not just admin-tier): this lists active games across EVERY
    # chat the bot is in, which would leak other groups' chat IDs/activity
    # to a random group admin if opened up like /admin and /stopgame are.
    async with get_session() as session:
        result = await session.execute(
            select(Game).where(Game.status.in_([GameStatus.QUEUE, GameStatus.IN_PROGRESS]))
        )
        games = list(result.scalars().all())
    if not games:
        await update.effective_message.reply_text("No active games anywhere.")
        return
    lines = [f"#{g.id} chat={g.chat_id} [{g.game_type.value}] {g.status.value}" for g in games]
    await update.effective_message.reply_text("🌍 Active games:\n" + "\n".join(lines))


@_admin_only
async def stopgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(Game)
            .where(
                Game.chat_id == update.effective_chat.id,
                Game.status.in_([GameStatus.QUEUE, GameStatus.IN_PROGRESS]),
            )
            .order_by(Game.id.desc())
        )
        game = result.scalars().first()
        if game is None:
            await update.effective_message.reply_text("No active game in this chat.")
            return
        await engine.owner_reset_game(session, game)
        await record_admin_action(session, update.effective_user.id, "stopgame", chat_id=update.effective_chat.id, game_id=game.id)
        await session.commit()
    await update.effective_message.reply_text("🛑 Game stopped.")


# --------------------------------------------------------------------------- #
# Panel callbacks
# --------------------------------------------------------------------------- #
async def _get_target_game(session: AsyncSession, chat_id: int) -> Game | None:
    result = await session.execute(
        select(Game)
        .where(Game.chat_id == chat_id, Game.status == GameStatus.IN_PROGRESS)
        .order_by(Game.id.desc())
    )
    return result.scalars().first()


NEEDS_INPUT = {
    "owner:setnumber": "setnumber",
    "owner:addruns": "addruns",
    "owner:setplayer": "setplayer",
}


async def owner_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not is_owner(user.id):
        await query.answer("⚠️ Owner only.", show_alert=True)
        return
    action = query.data

    async with get_session() as session:
        game = await _get_target_game(session, update.effective_chat.id)
        if game is None and action not in ("owner:view",):
            await query.answer("No in-progress game in this chat.", show_alert=True)
            return

        if action == "owner:forcewicket":
            resolution = await engine.owner_force_wicket(session, game)
            await record_admin_action(session, user.id, "force_wicket", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("⚡ Wicket forced!")
            await _refresh_after_cheat(context, session, game, resolution)

        elif action == "owner:skipball":
            await engine.owner_skip_ball(session, game)
            await record_admin_action(session, user.id, "skip_ball", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("⏭️ Ball skipped.")
            await _refresh_status(context, session, game)

        elif action == "owner:skipover":
            await engine.owner_skip_over(session, game)
            await record_admin_action(session, user.id, "skip_over", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("⏭️ Over skipped.")
            await _refresh_status(context, session, game)

        elif action == "owner:reset":
            await engine.owner_reset_game(session, game)
            await record_admin_action(session, user.id, "reset_game", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("🔄 Game reset.")
            try:
                await context.bot.send_message(chat_id=game.chat_id, text="🔄 The game was reset by an admin.")
            except TelegramError:
                pass

        elif action == "owner:end":
            winner = await engine.owner_end_game(session, game)
            await record_admin_action(session, user.id, "end_game", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("🛑 Game ended.")
            players = await engine.get_players(session, game.id)
            try:
                await context.bot.send_message(
                    chat_id=game.chat_id, text=final_result_text(game, players, winner), parse_mode="HTML"
                )
            except TelegramError:
                pass

        elif action == "owner:lock":
            await engine.owner_set_lock(session, game, not game.locked)
            await record_admin_action(session, user.id, "toggle_lock", detail=str(not game.locked), chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("🔐 Lock toggled: " + ("ON" if game.locked else "OFF"))

        elif action == "owner:view":
            if game is None:
                await query.answer()
                await query.edit_message_text("No in-progress game in this chat.", reply_markup=owner_panel_keyboard())
                return
            players = await engine.get_players(session, game.id)
            roster = "\n".join(f"  {p.display_name}: {p.runs}/{p.balls_faced} out={p.is_out}" for p in players)
            text = (
                f"📊 <b>Live Game #{game.id}</b>\n"
                f"Status: {game.status.value} | Locked: {game.locked}\n"
                f"Over: {game.current_over} Ball: {game.balls_this_over}/{game.balls_per_over}\n"
                f"Batter: {game.current_batter_id} | Bowler: {game.current_bowler_id}\n"
                f"Pending: bat={game.pending_batter_number} bowl={game.pending_bowler_number}\n\n{roster}"
            )
            await query.answer()
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=owner_panel_keyboard())
            return

        elif action in NEEDS_INPUT:
            context.user_data["awaiting_owner_input"] = (NEEDS_INPUT[action], update.effective_chat.id, game.id)
            await query.answer()
            prompts = {
                "setnumber": "Send the number (1-6) to force for the *next* resolved ball, as `bat:N` or `bowl:N`.",
                "addruns": "Send how many runs to add to the current batter (can be negative).",
                "setplayer": "Send the numeric Telegram user ID to set as the current batter.",
            }
            await query.edit_message_text(prompts[NEEDS_INPUT[action]], reply_markup=owner_panel_keyboard())
            return

        elif action == "owner:forceresult":
            winner = await engine.owner_end_game(session, game)
            await record_admin_action(session, user.id, "force_result", chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await query.answer("🎯 Result forced.")
            players = await engine.get_players(session, game.id)
            try:
                await context.bot.send_message(
                    chat_id=game.chat_id, text=final_result_text(game, players, winner), parse_mode="HTML"
                )
            except TelegramError:
                pass
        else:
            await query.answer()
            return

    try:
        await query.edit_message_reply_markup(reply_markup=owner_panel_keyboard())
    except TelegramError:
        pass


async def owner_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.get("awaiting_owner_input")
    if not pending or pending[1] != update.effective_chat.id:
        return
    kind, _chat_id, game_id = pending
    context.user_data.pop("awaiting_owner_input", None)
    text = update.effective_message.text.strip()

    async with get_session() as session:
        game = await engine.get_game_by_id(session, game_id)
        if game is None or game.status != GameStatus.IN_PROGRESS:
            await update.effective_message.reply_text("That game is no longer in progress.")
            return

        if kind == "addruns":
            try:
                runs = int(text)
            except ValueError:
                await update.effective_message.reply_text("⚠️ Send a whole number.")
                return
            batter = await engine.owner_add_runs(session, game, runs)
            await record_admin_action(session, update.effective_user.id, "add_runs", detail=str(runs), chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await update.effective_message.reply_text(f"💰 {batter.display_name} now has {batter.runs} runs.")
            await _refresh_status(context, session, game)

        elif kind == "setplayer":
            try:
                target_id = int(text)
            except ValueError:
                await update.effective_message.reply_text("⚠️ Send a numeric Telegram user ID.")
                return
            try:
                player = await engine.owner_set_current_batter(session, game, target_id)
            except engine.GameError as exc:
                await update.effective_message.reply_text(f"⚠️ {exc}")
                return
            await record_admin_action(session, update.effective_user.id, "set_player", detail=str(target_id), chat_id=game.chat_id, game_id=game.id)
            await session.commit()
            await update.effective_message.reply_text(f"👤 Current batter set to {player.display_name}.")
            await _refresh_status(context, session, game)

        elif kind == "setnumber":
            await update.effective_message.reply_text(
                "🎲 Noted - number overrides apply narratively; use ⚡ Force Wicket or 💰 Add Runs "
                "for guaranteed outcomes on the next ball."
            )
            await record_admin_action(session, update.effective_user.id, "set_number", detail=text, chat_id=game.chat_id, game_id=game.id)
            await session.commit()


owner_input_filter = filters.TEXT & ~filters.COMMAND


# --------------------------------------------------------------------------- #
# Shared refresh helpers
# --------------------------------------------------------------------------- #
async def _refresh_status(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game) -> None:
    if not game.status_message_id:
        return
    players = await engine.get_players(session, game.id)
    by_id = {p.user_id: p for p in players}
    batter = by_id.get(game.current_batter_id)
    bowler = by_id.get(game.current_bowler_id)
    if not batter or not bowler:
        return
    from app.database.models import Ball as BallModel

    result = await session.execute(select(BallModel).where(BallModel.game_id == game.id).order_by(BallModel.id))
    balls = list(result.scalars().all())
    text = status_text(game, batter, bowler, balls, waiting_on_dm=True)
    try:
        await context.bot.edit_message_text(
            chat_id=game.chat_id, message_id=game.status_message_id, text=text, parse_mode="HTML"
        )
    except TelegramError:
        pass


async def _refresh_after_cheat(context: ContextTypes.DEFAULT_TYPE, session: AsyncSession, game: Game, resolution) -> None:
    if resolution.is_game_over:
        from app.handlers.solo import _finish_game

        if game.game_type.value == "solo":
            await _finish_game(context, session, game, resolution)
        else:
            try:
                await context.bot.send_message(
                    chat_id=game.chat_id, text=final_result_text(game, resolution.all_players, resolution.winner), parse_mode="HTML"
                )
            except TelegramError:
                pass
        return
    await _refresh_status(context, session, game)
