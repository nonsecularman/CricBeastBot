"""
Configurable media rotation - single source of truth for which animation
plays for which game event, so no other module needs to hard-code a URL.
"""
from __future__ import annotations

import logging
import random

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
BATTER_TURN = "batter_turn"


def media_for(event: str) -> str | None:
    """Picks ONE random URL from that event's list (or None if nothing is configured)."""
    urls = settings.media.for_event(event)
    return random.choice(urls) if urls else None


async def send_event_media(message_send_video, event: str, caption: str | None = None) -> Message | None:
    """
    message_send_video: a bound callable like `bot.send_video` or
    `context.bot.send_video` partially applied with chat_id, i.e.
    `functools.partial(context.bot.send_video, chat_id=chat_id)`.

    Falls back silently (returns None) if no media is configured or Telegram
    rejects the URL - callers should never assume this succeeds. A random
    URL is picked each call, so add more comma-separated URLs to the env var
    for that event any time to widen the rotation - no code changes needed.
    """
    url = media_for(event)
    if not url:
        return None
    try:
        return await message_send_video(video=url, caption=caption, parse_mode="HTML")
    except TelegramError as exc:
        logger.warning("Failed to send media for event=%s url=%s: %s", event, url, exc)
        return None
