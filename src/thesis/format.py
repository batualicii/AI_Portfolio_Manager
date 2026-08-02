"""Render theses, falsifier checks and the weekly summary for Telegram.

Pure formatting over already-computed values. Every number arriving here was
produced by deterministic code (SPEC section 1); nothing in this module decides
anything, and nothing invents a figure.

The tone is deliberate. This system exists because the owner's behaviour, not its
signal, is what it can actually improve — so an alert states what changed and
what the owner themselves wrote, and stops. It does not advise, and it never says
"consider selling": the moment it starts recommending, it is the thing two
generations of evidence in this repo say does not work.
"""
from __future__ import annotations

from datetime import datetime

from src.bot.markdown import escape_md
from src.models import Thesis
from src.portfolio.guardrails import Breach, TradePace
from src.thesis.interpret import (
    WITHIN_SECTOR_ONLY,
    Check,
    Reading,
    checklist_line,
)
from src.thesis.monitor import FalsifierCheck

_MARK = {True: "🔴", False: "🟢"}


def _status(check: FalsifierCheck) -> str:
    if not check.evaluated:
        return "⚪"          # unknown — deliberately not the same glyph as fine
    return _MARK[check.fired]


def format_thesis(thesis: Thesis, checks: list[FalsifierCheck] | None = None) -> str:
    """One thesis and, if checked, where each of its conditions stands."""
    age = (datetime.now(thesis.opened_at.tzinfo) - thesis.opened_at).days
    lines = [
        f"*{escape_md(thesis.symbol)}* ({thesis.market.value}) · "
        f"conviction {thesis.conviction}/5 · held {age}d",
        "",
        escape_md(thesis.summary),
    ]

    if thesis.closed_at is not None:
        lines += ["", f"_Closed {thesis.closed_at:%Y-%m-%d}: "
                      f"{escape_md(thesis.closed_reason)}_"]
        return "\n".join(lines)

    lines += ["", "*What would prove this wrong:*"]
    for i, f in enumerate(thesis.falsifiers):
        check = checks[i] if checks and i < len(checks) else None
        mark = _status(check) if check else "·"
        lines.append(f"{mark} {escape_md(f.text)}")
        if check:
            lines.append(f"    _{escape_md(check.detail)}_")

    lines += ["", f"_Next review {thesis.review_due_on():%d %b %Y}_"]
    return "\n".join(lines)


def format_alert(thesis: Thesis, fired: list[FalsifierCheck]) -> str:
    """The one message that interrupts the owner: something they wrote came true.

    Quotes the condition back in their own words. The point is not that the bot
    noticed something — it is that the calm version of them left this behind, and
    it has now happened.
    """
    head = (f"⚠️ *{escape_md(thesis.symbol)}* — "
            f"{len(fired)} condition{'s' if len(fired) > 1 else ''} you wrote at "
            f"purchase {'have' if len(fired) > 1 else 'has'} now been met")
    lines = [head, ""]
    for check in fired:
        lines.append(f"🔴 _{escape_md(check.falsifier.text)}_")
        lines.append(f"    {escape_md(check.detail)}")
    lines += [
        "",
        escape_md("Your thesis was: ") + escape_md(thesis.summary),
        "",
        escape_md(
            "This is a fact, not advice. Nothing here knows whether to sell — "
            "it only knows that what you said would change your mind has "
            "happened. Reply with /close "
        ) + escape_md(thesis.symbol) + escape_md(" <reason> if you act on it."),
    ]
    return "\n".join(lines)


def format_review_prompt(theses: list[Thesis]) -> str:
    if not theses:
        return ""
    lines = ["*Reviews due*", ""]
    for t in theses:
        manual = [f.text for f in t.falsifiers if not f.checkable]
        lines.append(f"· *{escape_md(t.symbol)}* — {escape_md(t.summary[:110])}")
        for q in manual:
            # Manual falsifiers are questions no data can answer; review time is
            # the only moment they get asked, so they are asked explicitly.
            lines.append(f"    ❔ {escape_md(q)}")
    lines.append("")
    lines.append(escape_md("Reply /reviewed <SYMBOL> [note] once you have thought "
                           "about it — that timestamp is what stops reviews from "
                           "silently never happening."))
    return "\n".join(lines)


def format_weekly(
    valuation: str,
    theses: list[Thesis],
    due: list[Thesis],
    pace: TradePace,
    breaches: list[Breach],
    alerts: dict[str, list[FalsifierCheck]] | None = None,
    unknown: int = 0,
) -> str:
    """The weekly summary. Deliberately contains no buy or sell ideas."""
    when = datetime.now()
    parts = [f"📋 *Weekly review — {when:%d %b %Y}*", "", valuation]

    if alerts:
        parts += ["", "*Conditions met this week*"]
        for symbol, checks in alerts.items():
            for c in checks:
                parts.append(f"🔴 *{escape_md(symbol)}* — "
                             f"{escape_md(c.falsifier.text)}")
                parts.append(f"    _{escape_md(c.detail)}_")

    if theses:
        healthy = len(theses) - len(alerts or {})
        parts += ["", f"*{len(theses)} open theses* — {healthy} with nothing broken"]
        if unknown:
            # Never folded into "healthy": unchecked is not the same as intact,
            # and a monitor that blurs the two is worse than none.
            parts.append(f"⚪ {unknown} condition(s) could not be checked this week")
    else:
        parts += ["", escape_md("No theses recorded yet. /thesis add <SYMBOL> to "
                                "write down why you own something — a position "
                                "without one cannot be monitored.")]

    if breaches:
        parts += ["", "*Limits exceeded*"]
        for b in breaches:
            parts.append(f"· *{escape_md(b.name)}* — {escape_md(b.detail)}")

    parts += ["", "*Trading pace*", escape_md(pace.summary())]

    if due:
        parts += ["", format_review_prompt(due)]

    return "\n".join(parts)


def format_brief(brief, suggestions, questions,
                 readings: list[Reading] | None = None,
                 checks: list[Check] | None = None,
                 coverage: str | None = None) -> str:
    """A research page: facts, then the questions facts cannot settle."""
    f, price = brief.fundamentals, brief.price
    lines = [f"🔎 *{escape_md(brief.symbol)}* ({brief.market.value})", ""]

    if f.business_summary:
        text = f.business_summary.strip()
        lines += [escape_md(text[:600] + ("…" if len(text) > 600 else "")), ""]

    facts = []
    if f.market_cap:
        facts.append(f"cap {f.market_cap / 1e9:,.2f}B")
    if f.revenue_growth is not None:
        facts.append(f"revenue growth {f.revenue_growth * 100:.0f}%")
    if f.profit_margin is not None:
        facts.append(f"margin {f.profit_margin * 100:.0f}%")
    if f.pe_ratio:
        facts.append(f"P/E {f.pe_ratio:.0f}")
    if f.held_pct_institutions is not None:
        facts.append(f"institutions {f.held_pct_institutions * 100:.0f}%")
    if facts:
        lines += ["*Business*", escape_md(" · ".join(facts)), ""]

    if readings:
        lines += ["*What those numbers mean*"]
        for r in readings:
            lines.append(f"· *{escape_md(r.label)}* {escape_md(r.display)} — "
                         f"{escape_md(r.meaning)}")
            lines.append(f"    _{escape_md(r.context)}_")
        if any(r.peer_median is not None for r in readings):
            lines += ["", escape_md(WITHIN_SECTOR_ONLY)]
        elif coverage:
            # A blank where a comparison belongs reads as "nothing notable".
            lines += ["", escape_md(coverage)]
        lines.append("")

    if price is not None:
        bits = [f"{price.last:,.2f} now",
                f"{price.off_high_pct:+.0f}% off its 52w high"]
        if price.vs_200d_pct is not None:
            bits.append(f"{price.vs_200d_pct:+.0f}% vs its 200-day average")
        if price.momentum_12_1_pct is not None:
            bits.append(f"{price.momentum_12_1_pct:+.0f}% over 12 months")
        lines += ["*Price*", escape_md(" · ".join(bits))]
        if price.worst_drawdown_pct is not None:
            lines.append(escape_md(
                f"Worst fall in this window: {price.worst_drawdown_pct:.0f}%. "
                f"That is what owning it has felt like — assume you will see it again."
            ))
        lines.append("")

    if brief.news:
        lines += ["*Recent headlines*"]
        for item in brief.news:
            lines.append(f"· {escape_md(item.headline[:120])}")
        lines.append("")

    if brief.missing:
        # Silence about a gap reads as "nothing to report", which is a different
        # claim entirely — especially on BIST, where coverage is thin.
        lines += ["*Not available*", escape_md(", ".join(brief.missing)), ""]

    if suggestions:
        lines += ["*Falsifiers fitted to this name*"]
        for s in suggestions:
            lines.append(f"· `{escape_md(s.spec)}` — {escape_md(s.basis)}")
        lines.append("")

    if checks:
        lines += [f"*Checklist — {escape_md(checklist_line(checks))}*"]
        for c in checks:
            mark = "⚪" if c.passed is None else ("✅" if c.passed else "❌")
            lines.append(f"{mark} {escape_md(c.name)} — {escape_md(c.detail)}")
        lines += ["", escape_md(
            "This counts conditions met today. It is not a rating and predicts "
            "nothing — a composite score cannot be shown to work at this "
            "portfolio size, so this stays a list of facts rather than becoming "
            "a number you would trust more than it deserves."
        ), ""]

    lines += ["*What the numbers cannot tell you*"]
    for q in questions:
        lines.append(f"❔ {escape_md(q)}")
    lines += ["", escape_md(
        "No score and no verdict here on purpose — this repo measured that a "
        "ranking cannot be shown to work at this size. The advantage you have is "
        "answering the questions above better than a stranger could."
    )]
    return "\n".join(lines)


def _card_block(card, prefix: str = "", suffix: str = "") -> list[str]:
    """One name, four lines. The same four lines whether you own it or not."""
    head = f"{prefix}*{escape_md(card.symbol)}* · {escape_md(card.sector)}"
    if card.headline:
        head += " · " + escape_md(" · ".join(card.headline))
    head += suffix
    lines = [head, f"    _{escape_md(card.tally)}_"]

    for label, facts in (("ahead", card.ahead), ("behind", card.behind),
                         ("no ref", card.uncompared)):
        if facts:
            joined = " · ".join(f.render() for f in facts)
            lines.append(f"    `{label:<6}` {escape_md(joined)}")
    if card.also:
        lines.append(f"    `{'also':<6}` {escape_md(' · '.join(card.also))}")
    for warning in card.warnings:
        lines.append(f"    ⚠️ {escape_md(warning)}")
    return lines


def format_positions(cards, groups: dict[str, list], summary: list[str]) -> str:
    """Every holding, grouped by what has changed — never by what to do.

    The grouping is factual: a trend either is or is not broken. It stops short
    of "sell" deliberately. The rotation rule this repo tested would have sold
    NVDA after a 56% fall in 2018 and again after 66% in 2022, which is to say
    it would have sold precisely the drawdowns that had to be survived. A bot
    saying "sell" would reinstate that rule through the interface.
    """
    lines = [f"🧾 *Positions — {datetime.now():%d %b %Y}*", ""]
    if summary:
        lines += [escape_md(" · ".join(summary)), ""]

    for title, group in groups.items():
        if not group:
            continue
        lines.append(f"── *{escape_md(title)}* ({len(group)}) ──")
        lines.append("")
        for card in group:
            lines += _card_block(card)
            lines.append("")

    lines.append(escape_md(
        "Nothing here says sell. It says what changed, next to what you wrote "
        "down — and for anything with no thesis, that column is empty because "
        "you never filled it. /draft turns a rough sentence into one."
    ))
    return "\n".join(lines)


def format_pool(cards, market: str, built_at: str, new_symbols=None,
                footer: str = "") -> str:
    """The research queue, in the same words the portfolio is described in."""
    plural = "name" if len(cards) == 1 else "names"
    lines = [f"🎣 *Research pool — {escape_md(market)}*",
             escape_md(f"{len(cards)} {plural} · screened {built_at}"), ""]

    new_symbols = set(new_symbols or ())
    for i, card in enumerate(cards, 1):
        suffix = " 🆕" if card.symbol in new_symbols else ""
        lines += _card_block(card, prefix=f"{i}. ", suffix=suffix)
        lines.append("")

    if new_symbols:
        n = len(new_symbols)
        lines.append(escape_md(
            f"🆕 marks the {n} {'name' if n == 1 else 'names'} that "
            f"{'was' if n == 1 else 'were'} not in the previous screen. The rest "
            f"were already here last time — the list changes slowly, and that is "
            f"the honest shape of it."))
    if footer:
        lines.append(escape_md(footer))
    lines.append(escape_md(
        "Ordered by momentum, which decides reading order and nothing else — "
        "this repo measured that a price ranking cannot be validated at this "
        "size. These are names to read, not to buy. When you do buy, write the "
        "thesis first: /draft, then /thesis add."
    ))
    return "\n".join(lines)


def format_audit(rows) -> str:
    """Every position, with the one question that decides it.

    "Should I sell everything?" cannot be answered. "Would I buy this, today, at
    this weight?" can be answered once per position, and a position you cannot
    write a thesis for has already answered it.
    """
    lines = ["🧾 *Position audit*", "",
             escape_md("For each one: would you buy it today, at this weight, "
                       "knowing what you know now? A position you cannot write a "
                       "thesis for has answered that already."), ""]

    for row in rows:
        head = (f"*{escape_md(row['symbol'])}* ({row['market']}) · "
                f"{row['weight'] * 100:.1f}% of the book")
        if row.get("ceiling") and row["weight"] > row["ceiling"]:
            head += f" · over its {row['ceiling'] * 100:.0f}% ceiling"
        lines.append(head)

        if row.get("thesis"):
            lines.append(f"    _{escape_md(row['thesis'][:120])}_")
        else:
            lines.append(escape_md("    ⚠️ no thesis on record — nothing can be "
                                   "monitored, and nothing will alert you"))

        if row.get("facts"):
            lines.append(f"    {escape_md(row['facts'])}")
        if row.get("checks"):
            lines.append(f"    {escape_md(row['checks'])}")
        lines.append("")

    lines.append(escape_md(
        "Write a thesis for the ones you would buy again: /draft <MARKET> "
        "<SYMBOL> <price> <1-5> <why>. For the rest, the honest move is to say "
        "so out loud — /close records the reason, and that record is what makes "
        "the next decision better than this one."
    ))
    return "\n".join(lines)
