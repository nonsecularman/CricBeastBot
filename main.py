"""
CricBeastBot entrypoint.

Usage:
    python main.py

Reads all configuration from environment variables / .env (see
.env.example). Runs the bot with long-polling, which is the simplest and
most reliable deployment mode for a VPS (no public HTTPS endpoint needed).
"""
from __future__ import annotations

from app.bot import build_application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=["message", "callback_query", "my_chat_member"])


if __name__ == "__main__":
    main()
