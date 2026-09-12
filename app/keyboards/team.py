from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def team_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Create Team", callback_data="team:create")],
            [InlineKeyboardButton("🚪 Join Team", callback_data="team:joinmenu")],
            [InlineKeyboardButton("⚔️ Start Match", callback_data="team:startmatch")],
            [InlineKeyboardButton("📋 Team List", callback_data="team:list")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
        ]
    )


def team_join_keyboard(teams: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🏏 {name}", callback_data=f"team:join:{team_id}")]
        for team_id, name in teams
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)


def team_start_match_keyboard(teams: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🏏 {name}", callback_data=f"team:pick:{team_id}")]
        for team_id, name in teams
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)
