"""
Helper for sending private messages to a player, with graceful handling of
the case where the user has never started a DM with the bot (Telegram will
refuse to deliver in that case with a Forbidden error).
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Message
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger("cricbeast.dm")


class DMFailed(Exception):
    """Raised when a DM could not be delivered because the user hasn't started the bot."""


async def send_dm(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    try:
        return await context.bot.send_message(
            chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except Forbidden:
        logger.info("DM to %s failed: user has not started the bot", user_id)
        raise DMFailed(str(user_id))
    except TelegramError as exc:
        logger.warning("DM to %s failed: %s", user_id, exc)
        raise DMFailed(str(exc))
