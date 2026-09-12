"""Player profile, stats, and Hall of Fame display."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.database.database import get_session
from app.database.models import PlayerStat, User
from app.services.leaderboard import top_players
from app.utils.helpers import display_name


def _profile_text(user_label: str, stat: PlayerStat | None) -> str:
    if stat is None:
        return f"🏏 <b>Player Profile</b>\n\n👤 {user_label}\n\nNo matches played yet - jump into a Solo Game!"
    return (
        "🏏 <b>Player Profile</b>\n\n"
        f"👤 {user_label}\n\n"
        f"Matches: {stat.matches}\n"
        f"Wins: {stat.wins}\n"
        f"Losses: {stat.losses}\n"
        f"Runs: {stat.runs}\n"
        f"Wickets: {stat.wickets}\n"
        f"Highest Score: {stat.highest_score}\n"
        f"Tournaments Won: {stat.tournaments_won}\n"
        f"Win Rate: {stat.win_rate}%"
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        stat = await session.get(PlayerStat, user.id)
        await update.effective_message.reply_html(_profile_text(display_name(user), stat))


async def halloffame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        entries = await top_players(session, limit=10)
    if not entries:
        await update.effective_message.reply_text("🏅 The Hall of Fame is empty - be the first champion!")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, e in enumerate(entries):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        lines.append(f"{medal} {e.display_name or e.user_id} — {e.points} pts")
    text = "🏅 <b>CRICBEAST HALL OF FAME</b>\n\n🥇 <b>Top Players</b>\n\n" + "\n".join(lines)
    await update.effective_message.reply_html(text)


async def halloffame_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with get_session() as session:
        entries = await top_players(session, limit=10)
    from app.keyboards.main import back_cancel_keyboard

    if not entries:
        await query.edit_message_text(
            "🏅 The Hall of Fame is empty - be the first champion!", reply_markup=back_cancel_keyboard()
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{(medals[i] if i < 3 else f'{i+1}.')} {e.display_name or e.user_id} — {e.points} pts"
        for i, e in enumerate(entries)
    ]
    text = "🏅 <b>CRICBEAST HALL OF FAME</b>\n\n🥇 <b>Top Players</b>\n\n" + "\n".join(lines)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_cancel_keyboard())
