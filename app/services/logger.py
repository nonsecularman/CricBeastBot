"""
Secret game log: streams every resolved ball (with both hidden numbers) to
the configured LOG_CHANNEL_ID, and records every owner/admin action to the
admin_logs table. Never exposed to normal players.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import settings
from app.database.models import AdminLog, Ball, Game

logger = logging.getLogger("cricbeast.gamelog")


async def log_ball_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    ball: Ball,
    batter_name: str,
    bowler_name: str,
    score_after: int,
    wickets_after: int,
) -> None:
    if not settings.log_channel_id:
        return

    result_line = (
        f"💥 RESULT: OUT" if ball.result.value == "out" else f"📊 RESULT: +{ball.runs} RUNS"
    )
    text = (
        "🎯 <b>LIVE GAME LOG</b>\n\n"
        f"Game: {game.game_type.value.title()}\n"
        f"Chat: <code>{game.chat_id}</code>\n\n"
        f"🏏 Batter: {batter_name}\n"
        f"🥎 Bowler: {bowler_name}\n\n"
        f"🎯 Batter Number: {ball.batter_number}\n"
        f"🎯 Bowler Number: {ball.bowler_number}\n\n"
        f"{result_line}\n\n"
        f"Score: {score_after}/{wickets_after}\n"
        f"Ball: {ball.ball_number}/{game.balls_per_over}"
    )
    try:
        await context.bot.send_message(chat_id=settings.log_channel_id, text=text, parse_mode="HTML")
    except TelegramError as exc:
        logger.warning("Failed to deliver game log to LOG_CHANNEL_ID: %s", exc)


async def record_admin_action(
    session: AsyncSession,
    admin_id: int,
    action: str,
    detail: str = "",
    chat_id: int | None = None,
    game_id: int | None = None,
) -> None:
    session.add(
        AdminLog(admin_id=admin_id, action=action, detail=detail, chat_id=chat_id, game_id=game_id)
    )
    await session.flush()
    logger.info("ADMIN ACTION by %s: %s (%s)", admin_id, action, detail)
