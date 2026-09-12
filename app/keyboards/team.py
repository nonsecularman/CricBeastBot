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


def team_cap_choice_keyboard(exclude: set[str] | None = None) -> InlineKeyboardMarkup:
    """Cap picker for team creation. `exclude` removes a color already taken
    by the other team in this chat (Team A and Team B can never share a cap)."""
    exclude = exclude or set()
    row = []
    if "blue" not in exclude:
        row.append(InlineKeyboardButton("🔵 Blue Cap", callback_data="team:cap:blue"))
    if "red" not in exclude:
        row.append(InlineKeyboardButton("🔴 Red Cap", callback_data="team:cap:red"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)


def team_join_keyboard(team_a, team_b) -> InlineKeyboardMarkup:
    """team_a / team_b are Team ORM objects (or None if not created yet)."""
    rows = []
    if team_a is not None:
        emoji = "🔵" if team_a.cap_color == "blue" else "🔴"
        rows.append([InlineKeyboardButton(f"{emoji} TEAM A — {team_a.name}", callback_data=f"team:join:{team_a.id}")])
    if team_b is not None:
        emoji = "🔵" if team_b.cap_color == "blue" else "🔴"
        rows.append([InlineKeyboardButton(f"{emoji} TEAM B — {team_b.name}", callback_data=f"team:join:{team_b.id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)
