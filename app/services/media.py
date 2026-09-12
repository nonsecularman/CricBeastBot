"""
Configurable media rotation - single source of truth for which animation
plays for which game event, so no other module needs to hard-code a URL.
"""
from __future__ import annotations

import logging

from telegram import Message
from telegram.error import TelegramError

from app.config import settings

logger = logging.getLogger("cricbeast.media")

# Event names used throughout the game engine.
BOWLING = "bowling"
BATTING = "batting"
OUT = "out"
FOUR = "four"
SIX = "six"


def media_for(event: str) -> str | None:
    return settings.media.for_event(event)


async def send_event_media(message_send_video, event: str, caption: str | None = None) -> Message | None:
    """
    message_send_video: a bound callable like `bot.send_video` or
    `context.bot.send_video` partially applied with chat_id, i.e.
    `functools.partial(context.bot.send_video, chat_id=chat_id)`.

    Falls back silently (returns None) if no media is configured or Telegram
    rejects the URL - callers should never assume this succeeds.
    """
    url = media_for(event)
    if not url:
        return None
    try:
        return await message_send_video(video=url, caption=caption, parse_mode="HTML")
    except TelegramError as exc:
        logger.warning("Failed to send media for event=%s url=%s: %s", event, url, exc)
        return None
