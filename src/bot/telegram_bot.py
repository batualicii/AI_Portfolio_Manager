"""Telegram bot — the user's interface for keeping holdings in sync with Midas.

Stage 1 scope: import / list / update / remove positions, all owner-locked. The
daily digest (Stage 5) will reuse `send_message` and the same Application.

Security: every handler is wrapped by `_owner_only`, so the bot ignores anyone
whose Telegram id is not the configured owner. This is a personal, single-user bot.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from functools import wraps
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.config import Settings
from src.market.provider import MarketDataProvider
from src.models import Holding, Market
from src.portfolio.format import format_report
from src.portfolio.valuation import ValuationService
from src.reasoning.narrator import ClaudeNarrator
from src.signals.engine import SignalEngine
from src.signals.format import format_recommendations
from src.storage.db import Store

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


class PortfolioBot:
    """Wraps a python-telegram-bot Application with our command handlers."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        provider: MarketDataProvider,
        engine: SignalEngine,
        narrator: ClaudeNarrator | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._valuation = ValuationService(provider)
        self._engine = engine
        self._narrator = narrator
        self._scheduler: AsyncIOScheduler | None = None
        self._app: Application = (
            Application.builder()
            .token(settings.telegram_bot_token)
            .post_init(self._on_startup)
            .build()
        )
        self._register()

    async def _on_startup(self, _: Application) -> None:
        """Runs once inside the event loop: start the daily-digest scheduler."""
        tz = ZoneInfo(self._settings.digest_timezone)
        hh, mm = self._settings.digest_hour_minute
        self._scheduler = AsyncIOScheduler(timezone=tz)
        self._scheduler.add_job(
            self._daily_digest,
            CronTrigger(hour=hh, minute=mm, timezone=tz),
            name="daily_digest",
            misfire_grace_time=3600,  # still fire if the host was briefly asleep
        )
        self._scheduler.start()
        log.info(
            "Daily digest scheduled for %02d:%02d %s",
            hh, mm, self._settings.digest_timezone,
        )

    @property
    def app(self) -> Application:
        return self._app

    # ----------------------------- wiring -----------------------------

    def _register(self) -> None:
        self._app.add_handler(CommandHandler("start", self._owner_only(self._start)))
        self._app.add_handler(CommandHandler("help", self._owner_only(self._start)))
        self._app.add_handler(CommandHandler("add", self._owner_only(self._add)))
        self._app.add_handler(CommandHandler("remove", self._owner_only(self._remove)))
        self._app.add_handler(
            CommandHandler("holdings", self._owner_only(self._holdings))
        )
        self._app.add_handler(CommandHandler("value", self._owner_only(self._value)))
        self._app.add_handler(CommandHandler("scan", self._owner_only(self._scan)))
        self._app.add_handler(CommandHandler("digest", self._owner_only(self._digest_now)))
        self._app.add_handler(CommandHandler("log", self._owner_only(self._log)))

    def _owner_only(self, handler: Handler) -> Handler:
        """Reject everyone except the configured owner id."""
        owner = self._settings.telegram_owner_id

        @wraps(handler)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            user = update.effective_user
            if user is None or user.id != owner:
                log.warning("Ignoring message from non-owner id=%s", user and user.id)
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "This is a private bot."
                    )
                return
            await handler(update, ctx)

        return wrapper

    async def send_message(self, text: str) -> None:
        """Push a message to the owner (used by the daily digest scheduler)."""
        await self._app.bot.send_message(
            chat_id=self._settings.telegram_owner_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )

    # ---------------------------- handlers ----------------------------

    async def _start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "*AI Portfolio Manager* — advisory swing-trading assistant.\n\n"
            "Keep your Midas positions in sync with these commands:\n"
            "`/add US AAPL 10 185.50` — add/update 10 AAPL @ $185.50 avg\n"
            "`/add BIST THYAO 100 280` — add/update 100 THYAO @ 280 TRY\n"
            "`/remove US AAPL` — remove a position\n"
            "`/holdings` — list current positions\n"
            "`/value` — live prices, market value & unrealized P&L\n"
            "`/scan` — run the signal engine: ranked buy/sell/hold calls\n"
            "`/digest` — build today's full morning digest now\n"
            "`/log` — recent recommendations\n\n"
            "_The daily digest arrives automatically at "
            f"{self._settings.digest_time} {self._settings.digest_timezone}._",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/add <MARKET> <SYMBOL> <QTY> <AVG_COST> [note...]"""
        args = ctx.args or []
        if len(args) < 4:
            await update.effective_message.reply_text(
                "Usage: `/add <US|BIST> <SYMBOL> <QTY> <AVG_COST> [note]`\n"
                "Example: `/add US AAPL 10 185.50`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        market = _parse_market(args[0])
        if market is None:
            await update.effective_message.reply_text(
                "Market must be `US` or `BIST`.", parse_mode=ParseMode.MARKDOWN
            )
            return

        symbol = args[1].upper()
        qty = _parse_float(args[2])
        avg = _parse_float(args[3])
        if qty is None or avg is None or qty <= 0 or avg <= 0:
            await update.effective_message.reply_text(
                "Quantity and average cost must be positive numbers."
            )
            return

        note = " ".join(args[4:]) if len(args) > 4 else ""
        self._store.upsert_holding(
            Holding(symbol=symbol, market=market, quantity=qty, avg_cost=avg, note=note)
        )
        await update.effective_message.reply_text(
            f"✅ Saved *{symbol}* ({market.value}): {qty:g} @ "
            f"{avg:g} {market.currency}.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _remove(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/remove <MARKET> <SYMBOL>"""
        args = ctx.args or []
        if len(args) < 2:
            await update.effective_message.reply_text(
                "Usage: `/remove <US|BIST> <SYMBOL>`", parse_mode=ParseMode.MARKDOWN
            )
            return
        market = _parse_market(args[0])
        if market is None:
            await update.effective_message.reply_text("Market must be `US` or `BIST`.")
            return
        removed = self._store.remove_holding(args[1], market)
        msg = (
            f"🗑️ Removed *{args[1].upper()}* ({market.value})."
            if removed
            else f"No position found for {args[1].upper()} ({market.value})."
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _holdings(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        holdings = self._store.list_holdings()
        if not holdings:
            await update.effective_message.reply_text(
                "No positions yet. Add one with `/add US AAPL 10 185.50`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        lines = ["*Current holdings*"]
        for h in holdings:
            lines.append(
                f"• `{h.symbol}` ({h.market.value}) — {h.quantity:g} @ "
                f"{h.avg_cost:g} {h.market.currency}"
                + (f"  _{h.note}_" if h.note else "")
            )
        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    async def _value(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """Price every holding live and report market value + unrealized P&L."""
        holdings = self._store.list_holdings()
        if not holdings:
            await update.effective_message.reply_text(
                "No positions yet. Add one with `/add US AAPL 10 185.50`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        msg = await update.effective_message.reply_text("⏳ Pricing your portfolio…")
        # Fetching quotes is blocking I/O; run it off the event loop.
        report = await asyncio.to_thread(self._valuation.value, holdings)
        await msg.edit_text(format_report(report), parse_mode=ParseMode.MARKDOWN)

    async def _scan(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """Run the signal engine over holdings + watchlist and report ranked calls."""
        holdings = self._store.list_holdings()
        msg = await update.effective_message.reply_text(
            "🔍 Scanning markets… (pricing your holdings + watchlist, ~30s)"
        )
        recos = await asyncio.to_thread(self._engine.scan, holdings)
        # Enrich with plain-language rationale (explanation-only; falls back to
        # deterministic notes if the narrator is absent or the API errors).
        if self._narrator is not None:
            recos = await asyncio.to_thread(self._narrator.narrate, recos)
        # Persist to the audit trail so performance can be reviewed later.
        for r in recos:
            self._store.log_recommendation(r)
        held_keys = {(h.symbol.upper(), h.market) for h in holdings}
        text = format_recommendations(recos, header="Signal scan", held_keys=held_keys)
        await self._send_chunked(update, msg, text)

    async def _send_chunked(self, update, placeholder, text: str) -> None:
        """Send `text` respecting Telegram's 4096-char limit: edit the placeholder
        with the first chunk, then reply with any remaining chunks. Splits on line
        boundaries so Markdown stays valid within each message."""
        chunks = _split_for_telegram(text)
        await placeholder.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN)
        for chunk in chunks[1:]:
            await update.effective_message.reply_text(
                chunk, parse_mode=ParseMode.MARKDOWN
            )

    # ------------------------------ digest ------------------------------

    def build_digest(self) -> str:
        """Assemble the full morning digest: valuation + ranked signals.

        Blocking (prices, scan, narration, logging) — call via asyncio.to_thread.
        Every recommendation is persisted to the audit trail here.
        """
        holdings = self._store.list_holdings()
        report = self._valuation.value(holdings)
        recos = self._engine.scan(holdings)
        if self._narrator is not None:
            recos = self._narrator.narrate(recos)
        for r in recos:
            self._store.log_recommendation(r)

        held_keys = {(h.symbol.upper(), h.market) for h in holdings}
        now = datetime.now(ZoneInfo(self._settings.digest_timezone))
        header = (
            f"☀️ *Daily digest — {now:%a %d %b %Y, %H:%M} "
            f"{self._settings.digest_timezone}*"
        )
        valuation = format_report(report)
        signals = format_recommendations(
            recos, header="Today's signals", held_keys=held_keys
        )
        return f"{header}\n\n{valuation}\n\n{signals}"

    async def _daily_digest(self) -> None:
        """Scheduled 08:30 callback — build the digest and push it to the owner."""
        log.info("Building scheduled daily digest…")
        try:
            text = await asyncio.to_thread(self.build_digest)
        except Exception:  # noqa: BLE001 - never let a bad day kill the scheduler
            log.exception("Daily digest build failed")
            await self._app.bot.send_message(
                chat_id=self._settings.telegram_owner_id,
                text="⚠️ Daily digest failed to build today; will retry tomorrow.",
            )
            return
        for chunk in _split_for_telegram(text):
            await self._app.bot.send_message(
                chat_id=self._settings.telegram_owner_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _digest_now(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest — build and send the morning digest on demand (for testing)."""
        msg = await update.effective_message.reply_text("☀️ Building today's digest…")
        text = await asyncio.to_thread(self.build_digest)
        await self._send_chunked(update, msg, text)

    async def _log(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        recos = self._store.recent_recommendations(limit=10)
        if not recos:
            await update.effective_message.reply_text(
                "No recommendations logged yet. The signal engine arrives in Stage 3."
            )
            return
        lines = ["*Recent recommendations*"]
        for r in recos:
            when = r.created_at.strftime("%Y-%m-%d") if r.created_at else "?"
            lines.append(
                f"• {when} *{r.action.value}* `{r.symbol}` ({r.market.value}) "
                f"@ {r.price:g}"
            )
        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )


# --------------------------- small parse helpers ---------------------------

_TG_LIMIT = 4000  # safely under Telegram's 4096 hard cap


def _split_for_telegram(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Split text into <=limit chunks on line boundaries (keeps Markdown balanced)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip("\n"))
    return chunks


def _parse_market(raw: str) -> Market | None:
    try:
        return Market(raw.upper())
    except ValueError:
        return None


def _parse_float(raw: str) -> float | None:
    try:
        # tolerate "1,234.5" and "280,5" (TR decimal comma when no dot present)
        cleaned = raw.replace(",", "") if "." in raw else raw.replace(",", ".")
        return float(cleaned)
    except (ValueError, AttributeError):
        return None
