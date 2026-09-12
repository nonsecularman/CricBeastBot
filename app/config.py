"""
Central configuration for CricBeastBot.

Loads all runtime configuration from environment variables (via .env in local
dev). Nothing here should be hard-coded to a secret value in source control -
see .env.example for the full list of supported variables.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("cricbeast")


def _int_env(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Env var %s=%r is not a valid int, using default %s", name, raw, default)
        return default


@dataclass(frozen=True)
class MediaConfig:
    bowling: str | None = field(default_factory=lambda: os.getenv("MEDIA_BOWLING") or None)
    batting: str | None = field(default_factory=lambda: os.getenv("MEDIA_BATTING") or None)
    out: str | None = field(default_factory=lambda: os.getenv("MEDIA_OUT") or None)
    four: str | None = field(default_factory=lambda: os.getenv("MEDIA_FOUR") or None)
    six: str | None = field(default_factory=lambda: os.getenv("MEDIA_SIX") or None)

    def for_event(self, event: str) -> str | None:
        """event in {bowling, batting, out, four, six}"""
        return getattr(self, event, None)


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    bot_owner_id: int = _int_env("BOT_OWNER_ID", 0) or 0
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./cricbeast.db")
    log_channel_id: int | None = _int_env("LOG_CHANNEL_ID", None)

    solo_min_players: int = _int_env("SOLO_MIN_PLAYERS", 2) or 2
    solo_max_players: int = _int_env("SOLO_MAX_PLAYERS", 8) or 8

    rate_limit_actions: int = _int_env("RATE_LIMIT_ACTIONS", 8) or 8
    rate_limit_window: int = _int_env("RATE_LIMIT_WINDOW", 5) or 5

    media: MediaConfig = field(default_factory=MediaConfig)

    def validate(self) -> None:
        problems = []
        if not self.bot_token:
            problems.append("BOT_TOKEN is not set")
        if not self.bot_owner_id:
            problems.append("BOT_OWNER_ID is not set")
        if problems:
            raise RuntimeError(
                "Invalid configuration:\n  - " + "\n  - ".join(problems) +
                "\nCopy .env.example to .env and fill in the required values."
            )


settings = Settings()
