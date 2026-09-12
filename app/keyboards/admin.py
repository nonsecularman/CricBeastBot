from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def tournament_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Create Tournament", callback_data="tourney:create")],
            [InlineKeyboardButton("🔎 Browse Tournaments", callback_data="tourney:browse")],
            [InlineKeyboardButton("🚪 Join Tournament", callback_data="tourney:joinmenu")],
            [InlineKeyboardButton("📋 My Tournament", callback_data="tourney:mine")],
            [InlineKeyboardButton("▶️ Start Tournament", callback_data="tourney:startmenu")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")],
        ]
    )


def tournament_join_keyboard(tournaments: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🏆 {name}", callback_data=f"tourney:join:{tid}")]
        for tid, name in tournaments
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:tournament")])
    return InlineKeyboardMarkup(rows)


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎯 Force Result", callback_data="owner:forceresult"),
                InlineKeyboardButton("🎲 Set Number", callback_data="owner:setnumber"),
            ],
            [
                InlineKeyboardButton("⚡ Force Wicket", callback_data="owner:forcewicket"),
                InlineKeyboardButton("💰 Add Runs", callback_data="owner:addruns"),
            ],
            [
                InlineKeyboardButton("⏭️ Skip Ball", callback_data="owner:skipball"),
                InlineKeyboardButton("⏭️ Skip Over", callback_data="owner:skipover"),
            ],
            [
                InlineKeyboardButton("🔄 Reset Game", callback_data="owner:reset"),
                InlineKeyboardButton("🛑 End Game", callback_data="owner:end"),
            ],
            [
                InlineKeyboardButton("👤 Set Player", callback_data="owner:setplayer"),
                InlineKeyboardButton("📊 View Live Game", callback_data="owner:view"),
            ],
            [InlineKeyboardButton("🔐 Lock Game", callback_data="owner:lock")],
        ]
    )
