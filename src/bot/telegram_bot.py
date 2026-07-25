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
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.bot.markdown import escape_md
from src.config import Settings
from src.market.provider import MarketDataProvider
from src.models import Action, Holding, Market
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
        await self._send_to_owner(text)

    async def _send_to_owner(self, text: str) -> None:
        """Send with Markdown, falling back to plain text if Telegram rejects it.

        Telegram refuses the entire message when its Markdown does not balance.
        Dynamic text is escaped at the formatters, but this is the digest the
        user's morning depends on — an unreadable delivery beats no delivery.
        """
        try:
            await self._app.bot.send_message(
                chat_id=self._settings.telegram_owner_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except BadRequest as exc:
            log.warning("Markdown rejected (%s); resending as plain text.", exc)
            await self._app.bot.send_message(
                chat_id=self._settings.telegram_owner_id, text=text
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
            f"✅ Saved *{escape_md(symbol)}* ({market.value}): {qty:g} @ "
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
        symbol = escape_md(args[1].upper())
        msg = (
            f"🗑️ Removed *{symbol}* ({market.value})."
            if removed
            else f"No position found for {symbol} ({market.value})."
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
                f"• `{escape_md(h.symbol)}` ({h.market.value}) — {h.quantity:g} @ "
                f"{h.avg_cost:g} {h.market.currency}"
                + (f"  _{escape_md(h.note)}_" if h.note else "")
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
        narration_error = None
        if self._narrator is not None:
            recos, narration_error = await asyncio.to_thread(
                self._narrator.narrate, recos
            )
        # Persist to the audit trail so performance can be reviewed later.
        self._log_actionable(recos)
        held_keys = {(h.symbol.upper(), h.market) for h in holdings}
        text = format_recommendations(recos, header="Signal scan", held_keys=held_keys)
        text += _narration_warning(narration_error)
        await self._send_chunked(update, msg, text)

    async def _send_chunked(self, update, placeholder, text: str) -> None:
        """Send `text` respecting Telegram's 4096-char limit: edit the placeholder
        with the first chunk, then reply with any remaining chunks. Splits on line
        boundaries so Markdown stays valid within each message, and falls back to
        plain text per chunk if Telegram rejects the formatting."""
        chunks = _split_for_telegram(text)
        try:
            await placeholder.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN)
        except BadRequest as exc:
            log.warning("Markdown rejected (%s); resending as plain text.", exc)
            await placeholder.edit_text(chunks[0])
        for chunk in chunks[1:]:
            try:
                await update.effective_message.reply_text(
                    chunk, parse_mode=ParseMode.MARKDOWN
                )
            except BadRequest as exc:
                log.warning("Markdown rejected (%s); resending as plain text.", exc)
                await update.effective_message.reply_text(chunk)

    def _log_actionable(self, recos) -> None:
        """Record the calls that ask the user to do something.

        HOLDs are the steady state, not events: logging one per name per scan
        buries the actual decisions under standing advice and turns /log into a
        printout of a single morning. Repeats of the same call on the same day
        are collapsed too, since /scan is run on demand and the digest fires
        daily over the same signals.
        """
        for reco in recos:
            if reco.action is Action.HOLD:
                continue
            self._store.log_recommendation(reco, dedupe_same_day=True)

    # ------------------------------ digest ------------------------------

    def build_digest(self) -> str:
        """Assemble the full morning digest: valuation + ranked signals.

        Blocking (prices, scan, narration, logging) — call via asyncio.to_thread.
        Every recommendation is persisted to the audit trail here.
        """
        holdings = self._store.list_holdings()
        report = self._valuation.value(holdings)
        recos = self._engine.scan(holdings)
        narration_error = None
        if self._narrator is not None:
            recos, narration_error = self._narrator.narrate(recos)
        self._log_actionable(recos)

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
        return (
            f"{header}\n\n{valuation}\n\n{signals}"
            + _narration_warning(narration_error)
        )

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
            await self._send_to_owner(chunk)

    async def _digest_now(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest — build and send the morning digest on demand (for testing)."""
        msg = await update.effective_message.reply_text("☀️ Building today's digest…")
        text = await asyncio.to_thread(self.build_digest)
        await self._send_chunked(update, msg, text)

    async def _log(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        recos = self._store.recent_recommendations(limit=10)
        if not recos:
            await update.effective_message.reply_text(
                "No recommendations logged yet. Run /scan or /digest first."
            )
            return
        lines = ["*Recent recommendations*"]
        for r in recos:
            when = r.created_at.strftime("%Y-%m-%d") if r.created_at else "?"
            lines.append(
                f"• {when} *{r.action.value}* `{escape_md(r.symbol)}` "
                f"({r.market.value}) @ {r.price:g}"
            )
        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )


# --------------------------- small parse helpers ---------------------------

def _narration_warning(error: str | None) -> str:
    """Surface a broken narrator instead of quietly shipping the terse notes.

    The fallback output is perfectly usable, which is the problem: a narrator that
    fails every call is indistinguishable from one that was never configured, and
    that is how a stale SDK pin disabled this layer unnoticed.
    """
    if not error:
        return ""
    return f"\n\n⚠️ _Explanation layer unavailable ({error}); showing computed notes._"


_TG_LIMIT = 4000  # safely under Telegram's 4096 hard cap


def _split_for_telegram(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Split text into <=limit chunks on line boundaries (keeps Markdown balanced).

    A single line longer than the limit is hard-split rather than emitted whole:
    line-boundary splitting alone still produces an over-length chunk, which
    Telegram rejects outright. A very long LLM rationale can reach that size.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.rstrip("\n"))
        current = ""

    for line in text.split("\n"):
        while len(line) + 1 > limit:
            flush()
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit and current:
            flush()
        current += line + "\n"
    flush()
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
