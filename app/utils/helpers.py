"""Small formatting / misc helpers shared across handlers."""
from __future__ import annotations

from telegram import User as TgUser

SEPARATOR = "✦ ──────────── ✦"


def display_name(tg_user: TgUser) -> str:
    if tg_user.username:
        return f"@{tg_user.username}"
    return tg_user.first_name or str(tg_user.id)


def dm_deeplink(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start=hello"


def safe_html(text: str) -> str:
    """Escape a user-controlled string for safe inclusion in HTML-parsed messages."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def over_ball_string(numbers: list[int | str]) -> str:
    if not numbers:
        return "—"
    return " ".join(str(n) for n in numbers)


def mention_html(user_id: int, name: str) -> str:
    """
    Telegram HTML mention that pings/tags the user even without a username
    (works via tg://user?id=...). Used to tag the batter/bowler on every
    resolved ball.
    """
    return f'<a href="tg://user?id={user_id}">{safe_html(name)}</a>'
