"""Central configuration, loaded once from the environment (.env).

Keeping all settings in one typed object (instead of reading os.environ all over
the codebase) is the Single-Responsibility / Separation-of-Concerns split from the
project's design rules: every other module receives a Settings instance and never
touches the environment directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = two levels up from this file (src/config.py -> project/).
ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root, if present. Real env vars still win.
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised when a required setting is missing, with a clear, actionable message."""


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required setting '{name}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str
    telegram_owner_id: int

    # Anthropic (reasoning layer) — optional until Stage 5 (digest narration)
    anthropic_api_key: str | None
    anthropic_model: str

    # Optional news/sentiment
    finnhub_api_key: str | None

    # Behaviour
    digest_timezone: str
    digest_time: str  # "HH:MM"
    db_path: Path

    @property
    def digest_hour_minute(self) -> tuple[int, int]:
        hh, mm = self.digest_time.split(":")
        return int(hh), int(mm)

    @classmethod
    def load(cls) -> "Settings":
        """Build Settings from the environment, validating required fields."""
        owner_raw = _require("TELEGRAM_OWNER_ID")
        try:
            owner_id = int(owner_raw)
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_OWNER_ID must be a number, got '{owner_raw}'."
            ) from exc

        db_path = Path(os.getenv("DB_PATH", "data/portfolio.db"))
        if not db_path.is_absolute():
            db_path = ROOT / db_path

        finnhub = os.getenv("FINNHUB_API_KEY", "").strip() or None
        anthropic = os.getenv("ANTHROPIC_API_KEY", "").strip() or None

        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_owner_id=owner_id,
            anthropic_api_key=anthropic,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8").strip(),
            finnhub_api_key=finnhub,
            digest_timezone=os.getenv("DIGEST_TIMEZONE", "Europe/Istanbul").strip(),
            digest_time=os.getenv("DIGEST_TIME", "08:30").strip(),
            db_path=db_path,
        )
