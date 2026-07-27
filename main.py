"""Entry point — wire the pieces together and run the bot.

Builds storage, the market-data provider and (if a key is set) the Claude prose
layer, then hands them to the bot. There is no signal engine here any more: SPEC
section 0 records why producing buy/sell calls was removed rather than improved.
The engine still exists for the concluded experiments under scripts/research.
"""
from __future__ import annotations

import logging
import sys

from src.bot.telegram_bot import PortfolioBot
from src.config import ConfigError, Settings
from src.market.yahoo import YahooProvider
from src.reasoning.narrator import ClaudeNarrator
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

    narrator = None
    if settings.anthropic_api_key:
        narrator = ClaudeNarrator(settings)
        log.info("Claude narrator enabled (model %s).", settings.anthropic_model)
    else:
        log.info("No Anthropic key — the bot still runs; only prose is affected.")

    bot = PortfolioBot(settings, store, provider, narrator=narrator)
    log.info("Bot starting; weekly summary and daily falsifier check begin with "
             "the event loop.")

    try:
        # Blocks until Ctrl-C; handles its own asyncio loop.
        bot.app.run_polling()
    finally:
        store.close()
        log.info("Shut down cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
