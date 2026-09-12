"""
Permission checks. ALWAYS keyed off Telegram numeric user IDs - never
usernames, which can be changed or impersonated.
"""
from __future__ import annotations

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.config import settings


def is_owner(user_id: int) -> bool:
    return bool(settings.bot_owner_id) and user_id == settings.bot_owner_id


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if user_id is an admin/creator of the current chat (groups only)."""
    chat = update.effective_chat
    if chat is None or chat.type == Chat.PRIVATE:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def is_authorized_controller(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, game_creator_id: int
) -> bool:
    """
    Who may force-start / manage a game: the game's creator, a Telegram group
    admin of that chat, or the bot owner.
    """
    if user_id == game_creator_id or is_owner(user_id):
        return True
    return await is_group_admin(update, context, user_id)
