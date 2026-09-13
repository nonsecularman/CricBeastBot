"""
CricBeastBot entrypoint.

Usage:
    python main.py

Reads all configuration from environment variables / .env (see
.env.example). Runs the bot with long-polling, which is the simplest and
most reliable deployment mode for a VPS (no public HTTPS endpoint needed).
"""
from __future__ import annotations

import asyncio

from app.bot import build_application


def main() -> None:
    # Newer Python (3.10+ deprecated, 3.14 fully removed) no longer creates
    # an event loop implicitly in the main thread. Some python-telegram-bot
    # versions still call asyncio.get_event_loop() internally inside
    # run_polling(), expecting that old implicit behavior - which now raises
    # "RuntimeError: There is no current event loop" on very new Python.
    # Explicitly creating and setting a loop up front keeps run_polling()
    # working without needing to downgrade Python or the telegram library.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = build_application()
    application.run_polling(allowed_updates=["message", "callback_query", "my_chat_member"])


if __name__ == "__main__":
    main()
