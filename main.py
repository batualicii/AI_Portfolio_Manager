"""Entry point for the AI Portfolio Manager.

Stage 1: loads config, opens the store, and runs the owner-locked Telegram bot so
you can import and manage your Midas holdings. Later stages plug the signal engine,
reasoning layer, and 08:30 Europe/Istanbul digest scheduler into this same process.

Run:  python main.py
Stop: Ctrl-C
"""
from __future__ import annotations

import logging
import sys

from src.bot.telegram_bot import PortfolioBot
from src.config import ConfigError, Settings
from src.market.news import FinnhubNews
from src.market.provider import NullNewsProvider
from src.market.yahoo import YahooProvider
from src.reasoning.narrator import ClaudeNarrator
from src.signals.engine import SignalEngine
from src.storage.db import Store


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )
    # python-telegram-bot is chatty at INFO for network polling.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("main")

    try:
        settings = Settings.load()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 1

    store = Store(settings.db_path)
    log.info("Storage ready at %s", settings.db_path)

    provider = YahooProvider()
    news = (
        FinnhubNews(settings.finnhub_api_key)
        if settings.finnhub_api_key
        else NullNewsProvider()
    )
    if settings.finnhub_api_key:
        log.info("Finnhub news enabled.")
    else:
        log.info("No Finnhub key — sentiment signal is neutral (set FINNHUB_API_KEY to enable).")
    engine = SignalEngine(provider, news)

    narrator = None
    if settings.anthropic_api_key:
        narrator = ClaudeNarrator(settings)
        log.info("Claude narrator enabled (model %s).", settings.anthropic_model)
    else:
        log.info("No Anthropic key — recommendations use deterministic notes only.")

    bot = PortfolioBot(settings, store, provider, engine, narrator)
    log.info("Bot starting (digest scheduler starts with the event loop).")

    try:
        # Blocks until Ctrl-C; handles its own asyncio loop.
        bot.app.run_polling()
    finally:
        store.close()
        log.info("Shut down cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
