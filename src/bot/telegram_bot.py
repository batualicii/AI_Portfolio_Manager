"""Telegram bot — the owner's interface to their own reasoning.

It keeps holdings in sync with Midas, records why each position is owned, and
tells the owner when something they wrote at purchase has come true. It does not
issue buy or sell calls; SPEC section 0 explains why that was removed rather than
improved.

Two schedules, and the split is the point:

  * a **weekly** summary — positions, open theses, reviews due, trading pace. On a
    multi-year horizon there is nothing new to say most mornings, and a daily
    price message is itself a trading trigger.
  * a **daily silent check** of every falsifier, which sends nothing unless one
    fires. That is the only event worth interrupting someone for.

Security: every handler is wrapped by `_owner_only`, so the bot ignores anyone
whose Telegram id is not the configured owner. This is a personal, single-user bot.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
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
from src.market.provider import MarketDataProvider, NewsProvider
from src.models import Falsifier, FalsifierKind, Holding, Market, Thesis
from src.portfolio.format import format_report
from src.portfolio.guardrails import (
    CONVICTION_CEILING,
    concentration_breaches,
    max_weight_for,
    sale_without_cause,
    trade_pace,
)
from src.portfolio.scorecard import build_scorecard
from src.portfolio.valuation import ValuationService
from src.reasoning.narrator import ClaudeNarrator
from src.signals import indicators as ind
from src.signals.engine import SignalEngine
from src.storage.db import Store
from src.thesis.brief import OPEN_QUESTIONS, build_brief
from src.screen.pool import build_pool_cards, refresh_pool
from src.thesis.card import build_card
from src.thesis.format import (
    format_alert,
    format_audit,
    format_brief,
    format_pool,
    format_positions,
    format_scorecard,
    format_thesis,
    format_weekly,
)
from src.thesis.interpret import (
    checklist_line,
    peer_coverage,
    quality_checklist,
    read_fundamentals,
)
from src.thesis.suggest import suggest_falsifiers, suggested_command
from src.thesis.monitor import ThesisMonitor, fired, unevaluated

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


class PortfolioBot:
    """Wraps a python-telegram-bot Application with our command handlers."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        provider: MarketDataProvider,
        engine: SignalEngine | None = None,
        narrator: ClaudeNarrator | None = None,
        news: NewsProvider | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._valuation = ValuationService(provider)
        self._monitor = ThesisMonitor(provider)
        self._provider = provider
        # Headlines are research material for /brief, never a signal input.
        self._news = news
        # Optional and unused on the live path. The signal engine still exists
        # for the concluded experiments in scripts/research, but the bot no
        # longer asks it anything (SPEC section 0).
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
            self._weekly_summary,
            CronTrigger(day_of_week="sun", hour=hh, minute=mm, timezone=tz),
            name="weekly_summary",
            misfire_grace_time=3600,  # still fire if the host was briefly asleep
        )
        # Runs every day but stays silent unless a falsifier fires. A monitor
        # that speaks daily trains the owner to stop reading it.
        self._scheduler.add_job(
            self._falsifier_watch,
            CronTrigger(hour=hh, minute=mm, timezone=tz),
            name="falsifier_watch",
            misfire_grace_time=3600,
        )
        # Scanned weekly so /pool is never stale, delivered monthly so it is not
        # a trading trigger. A fresh list of twenty names every week is a machine
        # for manufacturing trades, and the one thing this repo can state with
        # confidence is that retail returns fall as trading rises.
        self._scheduler.add_job(
            self._scan_pool,
            CronTrigger(day_of_week="sat", hour=3, minute=0, timezone=tz),
            name="pool_scan",
            misfire_grace_time=6 * 3600,
        )
        self._scheduler.add_job(
            self._monthly_pool,
            CronTrigger(day=1, hour=hh, minute=mm, timezone=tz),
            name="monthly_pool",
            misfire_grace_time=6 * 3600,
        )
        self._scheduler.start()
        log.info(
            "Weekly summary Sundays %02d:%02d %s; silent falsifier check daily; "
            "pool screened Saturdays, delivered monthly",
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
        self._app.add_handler(CommandHandler("thesis", self._owner_only(self._thesis)))
        self._app.add_handler(CommandHandler("review", self._owner_only(self._review)))
        self._app.add_handler(
            CommandHandler("reviewed", self._owner_only(self._reviewed))
        )
        self._app.add_handler(CommandHandler("close", self._owner_only(self._close)))
        self._app.add_handler(CommandHandler("check", self._owner_only(self._check)))
        self._app.add_handler(CommandHandler("brief", self._owner_only(self._brief)))
        self._app.add_handler(CommandHandler("audit", self._owner_only(self._audit)))
        self._app.add_handler(
            CommandHandler("positions", self._owner_only(self._positions))
        )
        self._app.add_handler(CommandHandler("pool", self._owner_only(self._pool)))
        self._app.add_handler(CommandHandler("pass", self._owner_only(self._pass)))
        self._app.add_handler(
            CommandHandler("scorecard", self._owner_only(self._scorecard))
        )
        self._app.add_handler(CommandHandler("draft", self._owner_only(self._draft)))
        self._app.add_handler(CommandHandler("digest", self._owner_only(self._digest_now)))
        self._app.add_handler(CommandHandler("log", self._owner_only(self._log)))
        # /scan is deliberately absent. Producing ranked buy/sell calls is the
        # behaviour this design removed, not a feature waiting to be restored.

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
            "*AI Portfolio Manager*\n"
            "_It does not tell you what to buy. It remembers why you bought, and "
            "tells you when what you wrote comes true._\n\n"
            "*Positions*\n"
            "`/add US AAPL 10 185.50` · `/remove US AAPL` · `/holdings` · `/value`\n\n"
            "*Research*\n"
            "`/positions` — every holding, grouped by what has actually changed\n"
            "`/pool US` — the research queue, in the same words as your holdings\n"
            "`/pool US refresh` — rescan now (minutes)\n"
            "`/brief US ASTH` — everything knowable about a name, on one page\n"
            "`/audit` — every position you hold, and the question that decides it\n"
            "`/draft US ASTH 45 3 <rough thoughts>` — turns them into a ready line\n"
            "`/pass US ASTH too dear` — record a name you declined, and why\n"
            "`/scorecard` — did your picks beat the ones you passed on?\n\n"
            "*Theses*\n"
            "`/thesis` — list them\n"
            "`/thesis NVDA` — one thesis and where each condition stands\n"
            "`/thesis add US NVDA 100 4 | why I own it | trend:200/4 | growth:0.20`\n"
            "`/check` — run every condition now\n"
            "`/review` · `/reviewed NVDA [note]` · `/close NVDA <reason>`\n\n"
            "*Reports*\n"
            "`/digest` — the weekly summary now · `/log` — older signal history\n\n"
            f"_Weekly summary Sundays at {self._settings.digest_time} "
            f"{self._settings.digest_timezone}. Falsifiers are checked daily and "
            "you only hear about it if one fires. The pool is rescanned weekly "
            "but only sent on the 1st — a fresh list every week is a trading "
            "trigger, not information._",
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

    # ----------------------------- research -----------------------------

    async def _brief(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/brief <US|BIST> <SYMBOL> — assemble everything knowable about a name.

        The digging, done for you. The judgement is still yours, and the page
        ends with the questions that make that explicit.
        """
        args = ctx.args or []
        if len(args) < 2:
            await update.effective_message.reply_text(
                "Usage: `/brief <US|BIST> <SYMBOL>`", parse_mode=ParseMode.MARKDOWN)
            return
        market = _parse_market(args[0])
        if market is None:
            await update.effective_message.reply_text("Market must be US or BIST.")
            return
        symbol = args[1].upper()

        note = await update.effective_message.reply_text(f"Reading {symbol}…")
        brief = await asyncio.to_thread(
            build_brief, symbol, market, self._provider, self._news
        )
        suggestions = await asyncio.to_thread(
            suggest_falsifiers, symbol, market, self._provider
        )
        try:
            await note.delete()
        except Exception:  # noqa: BLE001 — cosmetic only
            pass
        sector = brief.fundamentals.sector
        readings = read_fundamentals(brief.fundamentals, sector, market)
        checks = quality_checklist(brief.fundamentals, brief.price)
        await self._send_to_owner(
            format_brief(brief, suggestions, OPEN_QUESTIONS, readings, checks,
                         peer_coverage(sector, market))
        )

    async def _audit(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/audit — every position you hold, with the question that decides it.

        Built for the moment a portfolio arrives from somewhere else with no
        theses attached: rather than asking the unanswerable "should I sell
        everything", it asks the answerable one once per position.
        """
        holdings = self._store.list_holdings()
        if not holdings:
            await update.effective_message.reply_text(
                "No positions recorded. `/add US AAPL 10 185.50` first.",
                parse_mode=ParseMode.MARKDOWN)
            return

        note = await update.effective_message.reply_text(
            f"Reading {len(holdings)} positions…")
        rows = await asyncio.to_thread(self._audit_rows, holdings)
        try:
            await note.delete()
        except Exception:  # noqa: BLE001 — cosmetic only
            pass
        await self._send_to_owner(format_audit(rows))

    def _audit_rows(self, holdings) -> list[dict]:
        """Blocking: prices, fundamentals and weights for every holding."""
        report = self._valuation.value(holdings)
        rate = report.usdtry or 1.0
        values, total = {}, 0.0
        for p in report.positions:
            mv = p.market_value
            if mv is None:
                continue
            in_try = mv * rate if p.holding.market is Market.US else mv
            values[(p.holding.symbol.upper(), p.holding.market)] = in_try
            total += in_try

        rows = []
        for holding in holdings:
            key = (holding.symbol.upper(), holding.market)
            weight = values.get(key, 0.0) / total if total else 0.0
            thesis = self._store.get_thesis(holding.symbol, holding.market)
            brief = build_brief(holding.symbol, holding.market, self._provider,
                               news=None)
            checks = quality_checklist(brief.fundamentals, brief.price)

            facts = []
            if brief.price is not None:
                facts.append(f"{brief.price.off_high_pct:+.0f}% off its 52w high")
                if brief.price.vs_200d_pct is not None:
                    facts.append(f"{brief.price.vs_200d_pct:+.0f}% vs 200-day")
            if brief.fundamentals.revenue_growth is not None:
                facts.append(
                    f"growth {brief.fundamentals.revenue_growth * 100:.0f}%")

            rows.append({
                "symbol": holding.symbol.upper(),
                "market": holding.market.value,
                "weight": weight,
                "ceiling": (max_weight_for(thesis.conviction) if thesis
                            else CONVICTION_CEILING[3]),
                "thesis": thesis.summary if thesis else None,
                "facts": " · ".join(facts),
                "checks": checklist_line(checks),
            })
        rows.sort(key=lambda r: r["weight"], reverse=True)
        return rows

    # ------------------- positions & pool, one renderer ------------------

    async def _positions(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/positions — every holding, grouped by what has actually changed."""
        holdings = self._store.list_holdings()
        if not holdings:
            await update.effective_message.reply_text(
                "No positions recorded. `/add US AAPL 10 185.50` first.",
                parse_mode=ParseMode.MARKDOWN)
            return

        note = await update.effective_message.reply_text(
            f"Reading {len(holdings)} positions…")
        cards, groups, summary = await asyncio.to_thread(
            self._position_cards, holdings)
        try:
            await note.delete()
        except Exception:  # noqa: BLE001 — cosmetic only
            pass
        await self._send_to_owner(format_positions(cards, groups, summary))

    def _position_cards(self, holdings):
        """Blocking: one card per holding, plus the grouping and the totals."""
        report = self._valuation.value(holdings)
        rate = report.usdtry or 1.0
        values, total = {}, 0.0
        prices = {}
        for p in report.positions:
            key = (p.holding.symbol.upper(), p.holding.market)
            prices[key] = p.price
            if p.market_value is None:
                continue
            in_try = (p.market_value * rate
                      if p.holding.market is Market.US else p.market_value)
            values[key] = in_try
            total += in_try

        cards, unreadable, changed, intact = [], [], [], []
        sector_weights: dict[str, float] = {}

        for holding in holdings:
            key = (holding.symbol.upper(), holding.market)
            weight = values.get(key, 0.0) / total if total else 0.0
            thesis = self._store.get_thesis(holding.symbol, holding.market)
            # Same window the screen reads, so a holding and a candidate are not
            # quietly being described over different stretches of history.
            brief = build_brief(holding.symbol, holding.market, self._provider,
                                news=None, period="2y")

            weeks_below = None
            broken = None
            try:
                bars = self._provider.get_history(
                    holding.symbol, holding.market, period="2y")
                close = ind.bars_to_frame(bars)["close"]
                below = ind.bars_below_ma(close, 200)
                weeks_below = below / 5 if below else None
                broken = ind.sustained_below_ma(close, 200, 20)
            except Exception:  # noqa: BLE001 — absence stays absence
                pass

            price = prices.get(key)
            since = ((price / holding.avg_cost - 1.0) * 100
                     if price and holding.avg_cost else None)

            card = build_card(
                holding.symbol, holding.market, brief.fundamentals, brief.price,
                sector=brief.fundamentals.sector,
                weeks_below_ma=weeks_below,
                weight=weight,
                since_entry_pct=since,
                thesis_summary=thesis.summary if thesis else None,
            )
            cards.append(card)
            sector_weights[card.sector] = sector_weights.get(card.sector, 0.0) + weight

            fired_now = []
            if thesis is not None:
                fired_now = fired(self._monitor.check(thesis))
                for check in fired_now:
                    card.warnings.append(f"fired: {check.falsifier.text}")

            if brief.price is None:
                unreadable.append(card)
            elif fired_now or broken:
                changed.append(card)
            else:
                intact.append(card)

        groups = {
            "Nothing has broken": intact,
            "Something changed": changed,
            "Could not be read": unreadable,
        }
        for group in groups.values():
            group.sort(key=lambda c: c.weight or 0.0, reverse=True)

        summary = [f"{len(holdings)} positions"]
        if total:
            summary.append(f"{total:,.0f} TRY")
        ranked = sorted(c.weight or 0.0 for c in cards)
        if len(ranked) >= 2:
            summary.append(f"top two {sum(ranked[-2:]) * 100:.0f}%")
        heaviest = max(sector_weights.items(), key=lambda kv: kv[1], default=None)
        if heaviest and heaviest[1] > 0.25:
            summary.append(f"{heaviest[0]} {heaviest[1] * 100:.0f}%")
        summary.append(f"{sum(1 for c in cards if c.has_thesis)}/{len(cards)} "
                       f"with a thesis")
        self._last_sector_weights = sector_weights
        return cards, groups, summary

    async def _pool(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/pool [US|BIST] [refresh] — the research queue, served from the last scan."""
        args = [a.upper() for a in (ctx.args or [])]
        market = _parse_market(args[0]) if args else Market.US
        if market is None:
            await update.effective_message.reply_text("Market must be US or BIST.")
            return

        if "REFRESH" in args:
            note = await update.effective_message.reply_text(
                "Screening the whole universe — this takes a few minutes.")
            try:
                kept, stored = await asyncio.to_thread(
                    refresh_pool, self._provider, self._store, market)
            except Exception as exc:  # noqa: BLE001
                await note.edit_text(f"Screen failed: {exc}")
                return
            await note.edit_text(f"{kept} names passed; {stored} kept for reading.")

        runs = self._store.pool_runs(market, limit=1)
        if not runs:
            await update.effective_message.reply_text(
                "No screen recorded yet. `/pool US refresh` runs one — it takes "
                "a few minutes.", parse_mode=ParseMode.MARKDOWN)
            return

        cards, declined = await asyncio.to_thread(
            build_pool_cards, self._store, market,
            getattr(self, "_last_sector_weights", {}),
            self._store.passed_symbols(market))
        built = runs[0][:10]
        footer = ""
        if not getattr(self, "_last_sector_weights", None):
            footer = ("Run /positions first and the pool will also flag names in "
                      "sectors you are already heavy in.")
        if not declined:
            footer += ("  /pass <MARKET> <SYMBOL> <why> on the ones you read and "
                       "reject — they stop coming back as new, and they are what "
                       "/scorecard measures your purchases against.")
        await self._send_to_owner(format_pool(
            cards, market.value, built, self._store.new_in_pool(market), footer,
            declined))

    # -------------------------- selection record -------------------------

    async def _pass(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/pass <US|BIST> <SYMBOL> <why> — record a name you looked at and declined.

        The unglamorous half of the record, and the half that makes the other
        half mean anything. Purchases alone can only be compared against the
        index; purchases against passes compare your judgement against the
        alternatives you actually had.
        """
        args = ctx.args or []
        if len(args) < 3:
            await update.effective_message.reply_text(
                "Usage: `/pass US ASTH too expensive for the growth`\n\n"
                "Records that you looked at it and said no, at today's price. "
                "That is what your purchases get compared against.",
                parse_mode=ParseMode.MARKDOWN)
            return

        market = _parse_market(args[0])
        if market is None:
            await update.effective_message.reply_text("Market must be US or BIST.")
            return
        symbol = args[1].upper()
        reason = " ".join(args[2:])

        quote = await asyncio.to_thread(self._provider.get_quote, symbol, market)
        price = quote.price if quote else None
        self._store.record_decision(symbol, market, "PASSED", price, reason)

        if price is None:
            await update.effective_message.reply_text(
                f"Recorded — but {symbol} could not be priced just now, so it "
                f"will sit outside the scorecard. Fine for remembering the "
                f"reason; useless for measuring the call.")
            return
        await update.effective_message.reply_text(
            f"Passed on {symbol} at {price:,.2f}. It will show up in /scorecard "
            f"as one of the names your purchases are measured against, and /pool "
            f"will stop treating it as new.")

    async def _scorecard(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/scorecard — did your picking beat your passing?"""
        note = await update.effective_message.reply_text("Pricing every decision…")
        card = await asyncio.to_thread(
            build_scorecard, self._store, self._provider)
        try:
            await note.delete()
        except Exception:  # noqa: BLE001 — cosmetic only
            pass
        await self._send_to_owner(format_scorecard(card))

    async def _draft(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/draft <US|BIST> <SYMBOL> <PRICE> <1-5> <your rough thoughts>

        Turns a messy note into a ready-to-send /thesis add line, with falsifiers
        fitted to the name. Claude tightens the sentence; it never supplies the
        reasoning, and if the note is too vague to tighten it says so rather than
        inventing something that will read convincingly back to you later.
        """
        args = ctx.args or []
        if len(args) < 5:
            await update.effective_message.reply_text(
                "Usage: `/draft <US|BIST> <SYMBOL> <PRICE> <1-5> <why, roughly>`\n\n"
                "Write it however it comes out — the point is to get your actual "
                "reasoning down, not to phrase it well.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        market = _parse_market(args[0])
        if market is None:
            await update.effective_message.reply_text("Market must be US or BIST.")
            return
        try:
            price, conviction = float(args[2]), int(args[3])
        except ValueError:
            await update.effective_message.reply_text(
                "Price must be a number and conviction an integer 1-5.")
            return

        symbol, raw = args[1].upper(), " ".join(args[4:])
        summary, error = raw, None
        if self._narrator is not None:
            summary, error = await asyncio.to_thread(
                self._narrator.draft_summary, raw
            )
        if summary is None:
            await update.effective_message.reply_text(f"⚠️ {error}")
            return

        suggestions = await asyncio.to_thread(
            suggest_falsifiers, symbol, market, self._provider
        )
        command = suggested_command(symbol, market, price, conviction,
                                    summary, suggestions)

        lines = ["Here is the line — read it, change anything you disagree with, "
                 "then send it back:", "", f"`{escape_md(command)}`", ""]
        if suggestions:
            lines.append("*Why these thresholds*")
            for s in suggestions:
                lines.append(f"· `{escape_md(s.spec)}` — {escape_md(s.basis)}")
        if error:
            lines += ["", escape_md(f"(Claude could not tighten the wording: "
                                    f"{error}. Your own words are used instead.)")]
        await self._send_to_owner("\n".join(lines))

    # ------------------------------ theses ------------------------------

    async def _thesis(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/thesis [SYMBOL] — show one thesis, or list them all.

        /thesis add <US|BIST> <SYMBOL> <PRICE> <CONVICTION 1-5> | <why> | <falsifier>...
        """
        args = ctx.args or []
        if args and args[0].lower() == "add":
            await self._thesis_add(update, " ".join(args[1:]))
            return

        if not args:
            theses = self._store.list_theses()
            if not theses:
                await update.effective_message.reply_text(
                    "No theses yet. `/thesis add US NVDA 100 4 | why I own it | "
                    "trend:200/4 | growth:0.20`\n\n"
                    "A position without a written thesis cannot be monitored — "
                    "there is nothing to check it against.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            lines = [f"*{len(theses)} open theses*", ""]
            for t in theses:
                lines.append(f"· *{t.symbol}* ({t.market.value}) conviction "
                             f"{t.conviction}/5 — next review "
                             f"{t.review_due_on():%d %b}")
            await self._send_to_owner("\n".join(lines))
            return

        symbol = args[0].upper()
        for market in (Market.US, Market.BIST):
            thesis = self._store.get_thesis(symbol, market)
            if thesis is not None:
                checks = await asyncio.to_thread(self._monitor.check, thesis)
                await self._send_to_owner(format_thesis(thesis, checks))
                return
        await update.effective_message.reply_text(f"No open thesis for {symbol}.")

    async def _thesis_add(self, update: Update, raw: str) -> None:
        """Parse and store a thesis. Refuses one with no checkable falsifier."""
        head, *rest = [part.strip() for part in raw.split("|")]
        fields = head.split()
        if len(fields) < 4 or not rest:
            await update.effective_message.reply_text(
                "Usage:\n`/thesis add <US|BIST> <SYMBOL> <PRICE> <1-5> | "
                "<why you own it> | <falsifier> [| <falsifier>...]`\n\n"
                "Falsifiers:\n"
                "`trend:200/4` — 4 weeks fully below the 200-day average\n"
                "`drawdown:0.5` — 50% below the high since you bought\n"
                "`growth:0.20` — revenue growth falls under 20%\n"
                "`margin:0.10` — profit margin falls under 10%\n"
                "`ask: a rival ships at scale` — for you to judge at review\n\n"
                "At least one must be checkable by code.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        market = _parse_market(fields[0])
        if market is None:
            await update.effective_message.reply_text("Market must be US or BIST.")
            return
        try:
            price, conviction = float(fields[2]), int(fields[3])
        except ValueError:
            await update.effective_message.reply_text(
                "Price must be a number and conviction an integer 1-5."
            )
            return

        summary, *falsifier_specs = rest
        falsifiers = []
        for spec in falsifier_specs:
            parsed = _parse_falsifier(spec)
            if parsed is None:
                await update.effective_message.reply_text(
                    f"Could not read falsifier `{spec}` — see /thesis add usage.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            falsifiers.append(parsed)

        thesis = Thesis(
            symbol=fields[1].upper(), market=market,
            opened_at=datetime.now(timezone.utc), entry_price=price,
            conviction=max(1, min(5, conviction)), summary=summary,
            falsifiers=tuple(falsifiers),
        )
        try:
            self._store.open_thesis(thesis)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — e.g. one already open
            await update.effective_message.reply_text(
                f"Could not record it: {exc}. There may already be an open thesis "
                f"for {thesis.symbol} — /close it first."
            )
            return
        # A purchase is half of the selection record. Without the passes on the
        # other side it compares against nothing, which is why /pass exists.
        self._store.record_decision(
            thesis.symbol, market, "BOUGHT", price, summary[:200])
        await self._send_to_owner(format_thesis(thesis))

    async def _check(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/check — run every falsifier now and report, fired or not."""
        theses = self._store.list_theses()
        if not theses:
            await update.effective_message.reply_text("No theses to check.")
            return
        for thesis in theses:
            checks = await asyncio.to_thread(self._monitor.check, thesis)
            await self._send_to_owner(format_thesis(thesis, checks))

    async def _review(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/review — which theses are due, with their unanswerable questions."""
        from src.thesis.format import format_review_prompt

        due = self._store.theses_due_for_review(datetime.now(timezone.utc))
        if not due:
            await update.effective_message.reply_text("Nothing due for review.")
            return
        await self._send_to_owner(format_review_prompt(due))

    async def _reviewed(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/reviewed <SYMBOL> [note] — timestamp a review you actually did."""
        args = ctx.args or []
        if not args:
            await update.effective_message.reply_text("Usage: `/reviewed <SYMBOL> [note]`",
                                                      parse_mode=ParseMode.MARKDOWN)
            return
        symbol = args[0].upper()
        for market in (Market.US, Market.BIST):
            thesis = self._store.get_thesis(symbol, market)
            if thesis is not None and thesis.id is not None:
                self._store.mark_reviewed(
                    thesis.id, datetime.now(timezone.utc), " ".join(args[1:])
                )
                await update.effective_message.reply_text(
                    f"{symbol} reviewed. Next due "
                    f"{thesis.review_due_on():%d %b %Y}."
                )
                return
        await update.effective_message.reply_text(f"No open thesis for {symbol}.")

    async def _close(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/close <SYMBOL> <reason> — record a sale, and whether anything broke."""
        args = ctx.args or []
        if len(args) < 2:
            await update.effective_message.reply_text(
                "Usage: `/close <SYMBOL> <reason>` — the reason is the point.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        symbol, reason = args[0].upper(), " ".join(args[1:])
        for market in (Market.US, Market.BIST):
            thesis = self._store.get_thesis(symbol, market)
            if thesis is None or thesis.id is None:
                continue
            checks = await asyncio.to_thread(self._monitor.check, thesis)
            note = sale_without_cause(len(fired(checks)), len(unevaluated(checks)))
            self._store.close_thesis(
                thesis.id, datetime.now(timezone.utc), reason,
                explained=note is None,
            )
            reply = f"Closed {symbol}: {reason}"
            if note:
                reply += "\n\n" + note
            await self._send_to_owner(reply)
            return
        await update.effective_message.reply_text(f"No open thesis for {symbol}.")

    # --------------------- weekly summary & watch ----------------------

    def build_digest(self) -> str:
        """The weekly summary. Blocking — call via asyncio.to_thread.

        Contains no buy or sell ideas. That is not an omission: producing them is
        the behaviour SPEC section 0 removed after two generations of evidence
        that it does not work.
        """
        holdings = self._store.list_holdings()
        report = self._valuation.value(holdings)
        theses = self._store.list_theses()

        alerts: dict[str, list] = {}
        unknown = 0
        for thesis in theses:
            checks = self._monitor.check(thesis)
            hit = fired(checks)
            if hit:
                alerts[thesis.symbol] = hit
            unknown += len(unevaluated(checks))

        now = datetime.now(timezone.utc)
        return format_weekly(
            valuation=format_report(report),
            theses=theses,
            due=self._store.theses_due_for_review(now),
            pace=trade_pace(self._store, days=90, now=now),
            breaches=concentration_breaches(report, self._store),
            alerts=alerts,
            unknown=unknown,
        )

    def check_falsifiers(self) -> list[str]:
        """Every open thesis, checked. Returns one message per thesis that fired.

        An empty list means nothing to say, and nothing gets sent. Silence is the
        correct output almost every day, and a monitor that speaks anyway trains
        the owner to stop reading it.
        """
        messages = []
        for thesis in self._store.list_theses():
            checks = self._monitor.check(thesis)
            hit = fired(checks)
            if not hit:
                continue
            if thesis.id is not None:
                for check in hit:
                    self._store.record_thesis_event(
                        thesis.id, "FIRED",
                        f"{check.falsifier.kind.value}: {check.detail}",
                    )
            messages.append(format_alert(thesis, hit))
        return messages

    async def _weekly_summary(self) -> None:
        """Scheduled Sunday callback."""
        try:
            text = await asyncio.to_thread(self.build_digest)
            await self._send_to_owner(text)
        except Exception:
            log.exception("Weekly summary failed")

    async def _falsifier_watch(self) -> None:
        """Scheduled daily callback that usually sends nothing at all."""
        try:
            messages = await asyncio.to_thread(self.check_falsifiers)
        except Exception:
            log.exception("Falsifier check failed")
            return
        for text in messages:
            await self._send_to_owner(text)

    async def _scan_pool(self) -> None:
        """Refresh both markets in the background. Never messages the owner."""
        for market in (Market.US, Market.BIST):
            try:
                kept, stored = await asyncio.to_thread(
                    refresh_pool, self._provider, self._store, market)
                log.info("Pool %s: %d passed, %d stored", market.value, kept, stored)
            except Exception:  # noqa: BLE001 — a failed scan keeps the old pool
                log.exception("Pool scan failed for %s", market.value)

    async def _monthly_pool(self) -> None:
        """Deliver the pool once a month, marking what is actually new."""
        for market in (Market.US, Market.BIST):
            if not self._store.pool_runs(market, limit=1):
                continue
            cards, declined = await asyncio.to_thread(
                build_pool_cards, self._store, market,
                getattr(self, "_last_sector_weights", {}),
                self._store.passed_symbols(market))
            if not cards and not declined:
                continue
            built = self._store.pool_runs(market, limit=1)[0][:10]
            await self._send_to_owner(format_pool(
                cards, market.value, built, self._store.new_in_pool(market),
                "", declined))

    async def _digest_now(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest — build the weekly summary on demand."""
        text = await asyncio.to_thread(self.build_digest)
        await self._send_to_owner(text)

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


def _parse_falsifier(spec: str) -> Falsifier | None:
    """Read one falsifier from the compact syntax used by /thesis add.

    Kept terse because it is typed on a phone, and typing is where good
    intentions die. `trend:200/4`, `drawdown:0.5`, `growth:0.20`, `margin:0.10`,
    or `ask: <question>` for something only a human can answer.
    """
    spec = spec.strip()
    key, _, rest = spec.partition(":")
    key, rest = key.strip().lower(), rest.strip()
    try:
        if key == "trend":
            days, _, weeks = rest.partition("/")
            return Falsifier(
                FalsifierKind.TREND_BREAK,
                f"closes below its {days or 200}-day average for "
                f"{weeks or 4} straight weeks",
                lookback=int(days or 200), persistence=int(weeks or 4),
            )
        if key == "drawdown":
            pct = float(rest)
            return Falsifier(
                FalsifierKind.DRAWDOWN,
                f"falls {pct * 100:.0f}% below its high since I bought",
                threshold=pct,
            )
        if key == "growth":
            pct = float(rest)
            return Falsifier(
                FalsifierKind.REVENUE_GROWTH,
                f"revenue growth drops under {pct * 100:.0f}%", threshold=pct,
            )
        if key == "margin":
            pct = float(rest)
            return Falsifier(
                FalsifierKind.PROFIT_MARGIN,
                f"profit margin drops under {pct * 100:.0f}%", threshold=pct,
            )
        if key == "ask" and rest:
            return Falsifier(FalsifierKind.MANUAL, rest)
    except ValueError:
        return None
    return None


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
