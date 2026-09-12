from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏏 Solo Game", callback_data="menu:solo")],
        [InlineKeyboardButton("👥 Team Game", callback_data="menu:team")],
        [InlineKeyboardButton("🏆 Tournament", callback_data="menu:tournament")],
        [InlineKeyboardButton("🏅 Hall of Fame", callback_data="menu:halloffame")],
        [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def back_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
        ]
    )
