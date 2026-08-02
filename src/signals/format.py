"""Render a ranked list of Recommendations in the CORE-SATELLITE frame (SPEC §0).

Two sections:
  * 🛰 Satellite — tactical BUY ideas for the active sleeve (keep it ≤ ~30% of capital).
  * 🏦 Core — risk review of existing holdings: hold for the long-run bull, but surface
    🔴 breakdown alerts (strong SELL signal) and 🟠 trims so the user can manage risk.

Reused by /scan and (Stage 6) the daily digest.
"""
from __future__ import annotations

from src.bot.markdown import escape_md
from src.models import Action, Market, Recommendation

# Suggested ceiling for the tactical sleeve, from the Stage 4 validation.
SATELLITE_BUDGET_PCT = 30


def _levels(r: Recommendation) -> str:
    bits = []
    if r.stop_loss is not None:
        bits.append(f"stop {r.stop_loss:g}")
    if r.take_profit is not None:
        bits.append(f"target {r.take_profit:g}")
    if r.suggested_weight:
        bits.append(f"size {r.suggested_weight * 100:.1f}%")
    return "  ·  " + " / ".join(bits) if bits else ""


def _cur(market: Market) -> str:
    return market.currency


def _clip(text: str, limit: int = 360) -> str:
    """Trim only very long deterministic-note fallbacks; one-sentence LLM prose fits.

    Escaped on the way out: rationales carry LLM prose and news headlines, and an
    unbalanced `_` or `*` in there makes Telegram reject the entire digest.
    """
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return escape_md(text)


def format_recommendations(
    recos: list[Recommendation], *, header: str, held_keys: set | None = None
) -> str:
    held_keys = held_keys or set()

    def is_held(r: Recommendation) -> bool:
        return (r.symbol.upper(), r.market) in held_keys

    buys = [r for r in recos if r.action is Action.BUY and not is_held(r)]
    sells = [r for r in recos if r.action is Action.SELL and is_held(r)]
    trims = [r for r in recos if r.action is Action.TRIM and is_held(r)]
    holds = [r for r in recos if r.action is Action.HOLD and is_held(r)]

    lines = [f"*{header}*"]

    # --- Satellite: tactical buy ideas ---
    lines.append(f"\n🛰 *Satellite — tactical buys* _(sleeve ≤ {SATELLITE_BUDGET_PCT}%)_")
    if buys:
        for r in buys:
            lines.append(
                f"🟢 *{escape_md(r.symbol)}* ({r.market.value}) @ {r.price:g} {_cur(r.market)}"
                f"{_levels(r)}  _score {r.score:+.2f}_"
            )
            if r.rationale:
                lines.append(f"   {_clip(r.rationale)}")
    else:
        lines.append("_No new tactical entries today._")

    # --- Core: risk review of holdings ---
    lines.append("\n🏦 *Core — holdings risk review*")
    if sells:
        for r in sells:
            lines.append(
                f"🔴 *Breakdown alert: {escape_md(r.symbol)}* ({r.market.value}) @ {r.price:g} "
                f"— signal {r.score:+.2f}. Consider trimming/exiting this position."
            )
            if r.rationale:
                lines.append(f"   {_clip(r.rationale)}")
    if trims:
        for r in trims:
            stop = f"  ·  stop {r.stop_loss:g}" if r.stop_loss else ""
            lines.append(
                f"🟠 *Weakening: {escape_md(r.symbol)}* ({r.market.value}) @ {r.price:g}"
                f"{stop}  _score {r.score:+.2f}_"
            )
            if r.rationale:
                lines.append(f"   {_clip(r.rationale)}")
    if holds:
        held = ", ".join(f"{escape_md(r.symbol)} ({r.score:+.2f})" for r in holds)
        lines.append(f"⚪ *Healthy holds:* {held}")
    if not (sells or trims or holds):
        lines.append("_No holdings tracked yet._")

    lines.append(
        "\n_Advisory only. Core = hold for the long-run; satellite = active sleeve. "
        "Every number is computed from price/volume; verify in Midas before trading._"
    )
    return "\n".join(lines)
