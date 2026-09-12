from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def spell_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔹 3 Balls", callback_data="solo:spell:3")],
            [InlineKeyboardButton("🔹 6 Balls", callback_data="solo:spell:6")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
        ]
    )


def solo_queue_keyboard(game_id: int, joinable: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if joinable:
        rows.append([InlineKeyboardButton("✅ Join", callback_data=f"solo:join:{game_id}")])
    rows.append(
        [
            InlineKeyboardButton("🚪 Leave", callback_data=f"solo:leave:{game_id}"),
            InlineKeyboardButton("▶️ Force Start", callback_data=f"solo:forcestart:{game_id}"),
        ]
    )
    rows.append([InlineKeyboardButton("❌ Cancel Game", callback_data=f"solo:cancelgame:{game_id}")])
    return InlineKeyboardMarkup(rows)


def number_choice_keyboard(prefix: str, game_id: int) -> InlineKeyboardMarkup:
    """1-6 number picker used both for DM bowling and group batting."""
    row1 = [
        InlineKeyboardButton(f"{n}️⃣", callback_data=f"{prefix}:{game_id}:{n}")
        for n in (1, 2, 3)
    ]
    row2 = [
        InlineKeyboardButton(f"{n}️⃣", callback_data=f"{prefix}:{game_id}:{n}")
        for n in (4, 5, 6)
    ]
    return InlineKeyboardMarkup([row1, row2])


def open_dm_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👉 Open Bowling DM", url=f"https://t.me/{bot_username}")]]
    )


def status_message_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🥎 Bowl in DM", url=f"https://t.me/{bot_username}")]]
    )
