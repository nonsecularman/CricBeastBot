"""/start, /help, /cancel and the main-menu callback router."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import ContextTypes

from app.database.database import get_session
from app.database.models import User
from app.keyboards.main import main_menu_keyboard
from app.utils.helpers import display_name

logger = logging.getLogger("cricbeast.start")

WELCOME_TEXT = (
    "🏏 <b>CricBeastBot</b>\n\n"
    "Welcome to the premium Telegram cricket experience!\n"
    "Pick a mode below to get started."
)

HELP_TEXT = (
    "🏏 <b>CricBeastBot — Help</b>\n\n"
    "<b>General</b>\n"
    "/start — open the main menu\n"
    "/help — this message\n"
    "/cancel — cancel your current action\n\n"
    "<b>Solo Game</b>\n"
    "/join — join the solo queue\n"
    "/leavesolo — leave the solo queue\n"
    "/startsolo — force-start the solo game\n\n"
    "<b>Team &amp; Tournament</b>\n"
    "/team — team game menu\n"
    "/tournament — tournament menu\n\n"
    "<b>Profile</b>\n"
    "/profile — your stats\n"
    "/stats — same as /profile\n"
    "/halloffame — top players\n"
)


async def _ensure_user(session: AsyncSession, tg_user) -> None:
    user = await session.get(User, tg_user.id)
    if user is None:
        session.add(
            User(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "",
                has_started_dm=False,
            )
        )
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name or user.first_name
    await session.flush()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    chat = update.effective_chat
    async with get_session() as session:
        await _ensure_user(session, tg_user)
        if chat and chat.type == "private":
            user = await session.get(User, tg_user.id)
            if user:
                user.has_started_dm = True
    await update.effective_message.reply_html(WELCOME_TEXT, reply_markup=main_menu_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(HELP_TEXT)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.effective_message.reply_text("❌ Cancelled.")


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles menu:main and menu:cancel - the rest are routed in their own modules."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "main":
        await query.edit_message_text(WELCOME_TEXT, parse_mode="HTML", reply_markup=main_menu_keyboard())
    elif action == "cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ Cancelled.")
