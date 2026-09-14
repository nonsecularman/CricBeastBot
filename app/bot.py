"""
Builds the python-telegram-bot Application and registers every handler.
"""
from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database.database import init_db
from app.handlers import admin, profile, solo, start, team, tournament

logger = logging.getLogger("cricbeast.bot")


async def _on_startup(application: Application) -> None:
    await init_db()
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Open the main menu"),
                BotCommand("help", "Show help"),
                BotCommand("join", "Join the solo queue"),
                BotCommand("leavesolo", "Leave the solo queue"),
                BotCommand("startsolo", "Force-start the solo game"),
                BotCommand("team", "Team game menu"),
                BotCommand("add_a", "Add a player to Team A (reply to them)"),
                BotCommand("add_b", "Add a player to Team B (reply to them)"),
                BotCommand("resetteams", "Clear Team A/B in this chat (admin/owner)"),
                BotCommand("claimhost", "Claim host rights over both teams"),
                BotCommand("tournament", "Tournament menu"),
                BotCommand("profile", "Your player profile"),
                BotCommand("stats", "Your player profile"),
                BotCommand("halloffame", "Top players"),
                BotCommand("cancel", "Cancel current action"),
            ]
        )
    except TelegramError as exc:
        logger.warning("Failed to set bot commands: %s", exc)
    logger.info("CricBeastBot is up. Owner ID=%s LogChannel=%s", settings.bot_owner_id, settings.log_channel_id)


async def _on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Never let one game's error crash the whole bot process.
    logger.exception("Unhandled exception while processing update: %s", update, exc_info=context.error)


def build_application() -> Application:
    settings.validate()
    application = Application.builder().token(settings.bot_token).post_init(_on_startup).build()

    # ---- Commands ---------------------------------------------------- #
    application.add_handler(CommandHandler("start", start.start_command))
    application.add_handler(CommandHandler("help", start.help_command))
    application.add_handler(CommandHandler("cancel", start.cancel_command))

    application.add_handler(CommandHandler("join", solo.join_command))
    application.add_handler(CommandHandler("leavesolo", solo.leavesolo_command))
    application.add_handler(CommandHandler("startsolo", solo.startsolo_command))

    application.add_handler(CommandHandler("team", team.team_command))
    application.add_handler(CommandHandler("add_a", team.add_a_command))
    application.add_handler(CommandHandler("add_b", team.add_b_command))
    application.add_handler(CommandHandler("resetteams", team.reset_teams_command))
    application.add_handler(CommandHandler("claimhost", team.claim_host_command))
    application.add_handler(CommandHandler("tournament", tournament.tournament_command))

    application.add_handler(CommandHandler("profile", profile.profile_command))
    application.add_handler(CommandHandler("stats", profile.profile_command))
    application.add_handler(CommandHandler("halloffame", profile.halloffame_command))

    application.add_handler(CommandHandler("admin", admin.admin_command))
    application.add_handler(CommandHandler("games", admin.games_command))
    application.add_handler(CommandHandler("stopgame", admin.stopgame_command))
    application.add_handler(CommandHandler("owner", admin.owner_command))
    application.add_handler(CommandHandler("cheat", admin.cheat_command))

    # ---- Main menu ----------------------------------------------------- #
    application.add_handler(CallbackQueryHandler(start.main_menu_callback, pattern=r"^menu:(main|cancel)$"))
    application.add_handler(CallbackQueryHandler(solo.solo_menu_callback, pattern=r"^menu:solo$"))
    application.add_handler(CallbackQueryHandler(team.team_menu_callback, pattern=r"^menu:team$"))
    application.add_handler(CallbackQueryHandler(tournament.tournament_menu_callback, pattern=r"^menu:tournament$"))
    application.add_handler(CallbackQueryHandler(profile.halloffame_menu_callback, pattern=r"^menu:halloffame$"))

    # ---- Solo game ------------------------------------------------------ #
    # NOTE: solo:spell:N is intercepted by team.match_spell_choice_callback,
    # which itself falls back to the plain solo flow when no team-match is
    # pending - see that function for details.
    application.add_handler(CallbackQueryHandler(team.match_spell_choice_callback, pattern=r"^solo:spell:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.join_callback, pattern=r"^solo:join:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.leave_callback, pattern=r"^solo:leave:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.forcestart_callback, pattern=r"^solo:forcestart:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.cancelgame_callback, pattern=r"^solo:cancelgame:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.bat_number_callback, pattern=r"^bat:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.bowl_number_callback, pattern=r"^bowl:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(solo.bowl_retry_callback, pattern=r"^bowl:retry:\d+$"))
    # Batting now happens by typing "1"-"6" as a plain group message (works
    # for Solo AND Team, same shared engine) - own group (4) so it always
    # gets a chance regardless of what the team/tournament name-input
    # handlers in groups 1-3 do with that update.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & filters.Regex(r"^[1-6]$"),
            solo.batter_text_number_message,
        ),
        4,
    )

    # ---- Team game -------------------------------------------------- #
    application.add_handler(CallbackQueryHandler(team.create_team_callback, pattern=r"^team:create$"))
    application.add_handler(CallbackQueryHandler(team.create_team_slot_callback, pattern=r"^team:createslot:(A|B)$"))
    application.add_handler(CallbackQueryHandler(team.join_team_menu_callback, pattern=r"^team:joinmenu$"))
    application.add_handler(CallbackQueryHandler(team.join_team_callback, pattern=r"^team:join:\d+$"))
    application.add_handler(CallbackQueryHandler(team.delete_team_menu_callback, pattern=r"^team:deletemenu$"))
    application.add_handler(CallbackQueryHandler(team.claim_host_callback, pattern=r"^team:claimhost$"))
    application.add_handler(CallbackQueryHandler(team.delete_team_callback, pattern=r"^team:delete:\d+$"))
    application.add_handler(CallbackQueryHandler(team.team_list_callback, pattern=r"^team:list$"))
    application.add_handler(CallbackQueryHandler(team.start_match_menu_callback, pattern=r"^team:startmatch$"))
    application.add_handler(
        MessageHandler(team.team_name_filter, team.team_name_message), 1
    )  # group 1: only fires when context.user_data['awaiting_team_name'] is set

    # ---- Tournament ---------------------------------------------------- #
    application.add_handler(CallbackQueryHandler(tournament.create_tournament_callback, pattern=r"^tourney:create$"))
    application.add_handler(CallbackQueryHandler(tournament.browse_tournaments_callback, pattern=r"^tourney:browse$"))
    application.add_handler(CallbackQueryHandler(tournament.join_tournament_menu_callback, pattern=r"^tourney:joinmenu$"))
    application.add_handler(CallbackQueryHandler(tournament.join_tournament_callback, pattern=r"^tourney:join:\d+$"))
    application.add_handler(CallbackQueryHandler(tournament.my_tournament_callback, pattern=r"^tourney:mine$"))
    application.add_handler(CallbackQueryHandler(tournament.start_tournament_menu_callback, pattern=r"^tourney:startmenu$"))
    application.add_handler(
        MessageHandler(tournament.tourney_name_filter, tournament.tourney_name_message), 2
    )  # group 2: only fires when context.user_data['awaiting_tourney_name'] is set

    # ---- Owner / Admin --------------------------------------------------- #
    application.add_handler(CallbackQueryHandler(admin.owner_panel_callback, pattern=r"^owner:"))
    application.add_handler(
        MessageHandler(admin.owner_input_filter, admin.owner_text_input), 3
    )  # group 3: only fires when context.user_data['awaiting_owner_input'] is set

    application.add_error_handler(_on_error)
    return application
