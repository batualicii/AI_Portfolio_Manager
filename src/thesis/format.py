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
