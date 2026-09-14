from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def team_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎙️ Claim Host", callback_data="team:claimhost")],
            [InlineKeyboardButton("➕ Create Team", callback_data="team:create")],
            [InlineKeyboardButton("🚪 Join Team", callback_data="team:joinmenu")],
            [InlineKeyboardButton("🗑️ Delete Team", callback_data="team:deletemenu")],
            [InlineKeyboardButton("⚔️ Start Match", callback_data="team:startmatch")],
            [InlineKeyboardButton("📋 Team List", callback_data="team:list")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
        ]
    )


def team_create_choice_keyboard(team_a_exists: bool, team_b_exists: bool) -> InlineKeyboardMarkup:
    """
    One button per free slot - picking it decides the cap AND the slot
    together in a single tap: Team A is always Blue Cap, Team B is always
    Red Cap.
    """
    rows = []
    if not team_a_exists:
        rows.append([InlineKeyboardButton("🔵 Create Team A (Blue Cap)", callback_data="team:createslot:A")])
    if not team_b_exists:
        rows.append([InlineKeyboardButton("🔴 Create Team B (Red Cap)", callback_data="team:createslot:B")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)


def team_join_keyboard(team_a, team_b) -> InlineKeyboardMarkup:
    """team_a / team_b are Team ORM objects (or None if not created yet)."""
    rows = []
    if team_a is not None:
        rows.append([InlineKeyboardButton(f"🔵 TEAM A — {team_a.name}", callback_data=f"team:join:{team_a.id}")])
    if team_b is not None:
        rows.append([InlineKeyboardButton(f"🔴 TEAM B — {team_b.name}", callback_data=f"team:join:{team_b.id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)


def team_delete_keyboard(team_a, team_b) -> InlineKeyboardMarkup:
    """team_a / team_b are Team ORM objects (or None if not created yet)."""
    rows = []
    if team_a is not None:
        rows.append([InlineKeyboardButton(f"🗑️ Delete Team A — {team_a.name}", callback_data=f"team:delete:{team_a.id}")])
    if team_b is not None:
        rows.append([InlineKeyboardButton(f"🗑️ Delete Team B — {team_b.name}", callback_data=f"team:delete:{team_b.id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:team")])
    return InlineKeyboardMarkup(rows)
