"""One compact block per name — and the same block for a holding and a candidate.

This is the module that decides whether the pool quietly beats the portfolio.

If candidates are described as *"strong growth, cheap, trending up"* and holdings
as *"7.2% of the book, no thesis"*, then every month the new name looks better
than the one already owned — not because it is, but because it was written up
more generously. The result is rotation, and rotation is the one thing this repo
has measured with any confidence: retail returns fall as trading rises
(Barber & Odean), and the momentum rotation tested here would have sold NVDA
after both of the drawdowns that preceded its best years.

So there is exactly one renderer, used by both sides. A candidate that is not
genuinely better than what you own cannot *look* better, because it is being
described with the same four facts, against the same references, in the same
words.

What a card is not: a rating. `3/4 ahead` counts how many metrics sit on the
better side of a reference today. It is a tally of comparisons, not a forecast,
and SPEC section 6c is why it never becomes a single number to sort on.

Nor are all four comparisons equally solid. A sector median built from six names
— which is every covered sector on BIST — moves if one company restates, and an
"ahead" resting on it is close to a coin flip. Those references are marked thin
in the line itself and counted separately in the tally, because the tally is the
most confident-looking thing on the card and would otherwise be the least
earned.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.market.types import Fundamentals
from src.models import Market
from src.thesis.interpret import THIN_SAMPLE, market_medians, sector_medians

# The last resort, used only when neither a sector nor a market median exists —
# an unbuilt stats file, or a metric nothing in the universe reported. Absolute
# lines are the weakest reference here and the card always says which one it
# used, so the reader is never left guessing what "ahead" was measured against.
ABSOLUTE = {
    "growth": 0.05,
    "margin": 0.0,
    "P/E": 25.0,
}


@dataclass(frozen=True)
class Fact:
    """One metric, its reference, and which side of it the company sits on."""
    label: str
    display: str
    reference: str
    ahead: bool | None          # None = no reference, so no comparison was made
    thin: bool = False          # the reference rests on too few names to lean on

    def render(self) -> str:
        return f"{self.label} {self.display} ({self.reference})"


@dataclass
class Card:
    symbol: str
    market: Market
    sector: str
    headline: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    also: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Portfolio-side only, and kept as numbers so callers sort and total on them
    # rather than parsing the rendered strings back out.
    weight: float | None = None
    has_thesis: bool = False
    # The one sentence that makes this position a decision rather than a row.
    # Derived mechanically from facts already on the card — never advice.
    tension: str = ""
    # True when the comparisons came from the whole market rather than peers,
    # so the reader is told once at the top instead of nine times.
    market_reference: bool = False

    @property
    def ahead(self) -> list[Fact]:
        return [f for f in self.facts if f.ahead is True]

    @property
    def behind(self) -> list[Fact]:
        return [f for f in self.facts if f.ahead is False]

    @property
    def uncompared(self) -> list[Fact]:
        return [f for f in self.facts if f.ahead is None]

    @property
    def thin_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.thin]

    @property
    def tally(self) -> str:
        rated = len(self.ahead) + len(self.behind)
        if not rated:
            return "nothing comparable"
        line = f"{len(self.ahead)}/{rated} ahead"
        if self.uncompared:
            line += f", {len(self.uncompared)} unmeasured"
        if self.thin_facts:
            # A tally that counted a six-name median the same as a hundred-name
            # one would be the most confident-looking part of the card and the
            # least earned. BIST is the case: four covered sectors, all n<10.
            line += f", {len(self.thin_facts)} on a thin sample"
        return line

    @property
    def trend_broken(self) -> bool | None:
        for f in self.facts:
            if f.label == "trend":
                return None if f.ahead is None else not f.ahead
        return None


def _fact(label: str, value: float | None, median, n, *, pct: bool,
          lower_is_better: bool = False, kind: str = "sector") -> Fact | None:
    if value is None:
        return None
    display = f"{value * 100:+.0f}%" if pct else f"{value:.0f}"
    if median is not None:
        ref_display = f"{median * 100:+.0f}%" if pct else f"{median:.0f}"
        # `kind` is "sector" or "market": the second mixes industries and is a
        # level rather than a peer comparison, so it is never printed as the
        # first. A bank's margin next to an airline's is not a like comparison.
        thin = n is not None and n < THIN_SAMPLE
        reference = f"{kind} {ref_display}, n={n}" + (", thin" if thin else "")
        ahead = value < median if lower_is_better else value > median
        return Fact(label, display, reference, bool(ahead), thin)
    else:
        line = ABSOLUTE.get(label)
        if line is None:
            return Fact(label, display, "no reference", None)
        ref_display = f"{line * 100:+.0f}%" if pct else f"{line:.0f}"
        # Named as a fixed line, never as "sector" — the reader must be able to
        # tell a peer comparison from a rule of thumb at a glance.
        reference = f"fixed line {ref_display}, no sector median"
        ahead = value < line if lower_is_better else value > line
    return Fact(label, display, reference, bool(ahead))


def build_card(
    symbol: str,
    market: Market,
    fundamentals: Fundamentals,
    price=None,
    *,
    sector: str | None = None,
    weeks_below_ma: float | None = None,
    weight: float | None = None,
    since_entry_pct: float | None = None,
    thesis_summary: str | None = None,
    cap_usd: float | None = None,
    institutional: float | None = None,
    crowded_sector_weight: float | None = None,
    ceiling: float | None = None,
    value_try: float | None = None,
) -> Card:
    """Assemble the block. Identical arguments produce an identical card.

    Everything optional is portfolio- or screen-specific context; the four rated
    facts are the same on both sides by construction.
    """
    sector = sector or fundamentals.sector or "Unknown"
    medians = sector_medians(sector, market)
    # Three references, in descending strength: this company's own sector, the
    # whole market, a fixed line. Which one was used is always visible in the
    # rendered text, so "ahead" never quietly changes meaning between names.
    reference_kind = "sector"
    if not medians:
        medians = market_medians(market)
        reference_kind = "market"
    card = Card(symbol.upper(), market, sector,
                weight=weight, has_thesis=thesis_summary is not None)

    if cap_usd:
        card.headline.append(f"cap {cap_usd / 1e9:,.2f}B")
    if value_try is not None:
        # Percentages alone are hard to weigh a decision against; the amount of
        # actual money in a position is what the decision is about.
        card.headline.append(f"{value_try:,.0f} TRY")
    if weight is not None:
        card.headline.append(f"{weight * 100:.1f}% of the book")
    if since_entry_pct is not None:
        card.headline.append(f"since entry {since_entry_pct:+.0f}%")

    for label, field_name, pct, lower in (
        ("growth", "revenue_growth", True, False),
        ("margin", "profit_margin", True, False),
        ("P/E", "pe_ratio", False, True),
    ):
        stat = medians.get(field_name) or {}
        fact = _fact(label, getattr(fundamentals, field_name, None),
                     stat.get("median"), stat.get("n"),
                     pct=pct, lower_is_better=lower, kind=reference_kind)
        if fact is not None:
            card.facts.append(fact)

    if price is not None and price.vs_200d_pct is not None:
        ref = "its own 200-day average"
        if weeks_below_ma:
            ref += f", {weeks_below_ma:.0f} weeks below"
        card.facts.append(Fact("trend", f"{price.vs_200d_pct:+.0f}%", ref,
                               price.vs_200d_pct > 0))

    if price is not None:
        if price.momentum_12_1_pct is not None:
            card.also.append(f"12m {price.momentum_12_1_pct:+.0f}%")
        if price.worst_drawdown_pct is not None:
            card.also.append(f"worst fall {price.worst_drawdown_pct:.0f}%")
    # A beta of 0.0 on an equity is not a low-beta stock, it is a missing value
    # the source rendered as a number — Yahoo does this routinely for BIST. It
    # showed up as "beta 0.0" and "beta -0.0" next to real readings, which is
    # worse than showing nothing.
    if fundamentals.beta is not None and abs(fundamentals.beta) >= 0.05:
        card.also.append(f"beta {fundamentals.beta:.1f}")
    if institutional is not None:
        card.also.append(f"institutions {institutional * 100:.0f}%")

    if not medians:
        card.warnings.append(
            "no medians for this market at all — compared against fixed lines, "
            "which is the weakest reference here"
        )
    elif reference_kind == "market":
        # Flagged, not spelled out per card. Nine identical paragraphs of caveat
        # buried the numbers they were meant to qualify; the reader is told once.
        card.market_reference = True
    if crowded_sector_weight:
        card.warnings.append(
            f"you already hold {crowded_sector_weight * 100:.0f}% in {sector}"
        )

    card.tension = _tension(card, since_entry_pct, ceiling, weeks_below_ma)
    return card


def _tension(card: Card, since: float | None, ceiling: float | None,
             weeks_below: float | None) -> str:
    """Name why this position is a decision, using only what is already shown.

    A page of figures is not a thought. The figures were all there — 27.3% of
    the book, up 308%, ahead on two of four — and the reader still could not say
    what any of it meant, because nothing put two of them next to each other.
    This does exactly that and stops: it never says buy, sell or hold, and every
    branch is a restatement of numbers on the same card.
    """
    weight, ahead, behind = card.weight, len(card.ahead), len(card.behind)

    if weight is not None and ceiling and weight > ceiling:
        over = f"{weight * 100:.0f}% of the book against a {ceiling * 100:.0f}% ceiling"
        if since is not None and since > 100:
            return (f"{over} — it got there by rising {since:+.0f}%, not by being "
                    f"sized there. Deciding to keep that weight is a separate "
                    f"decision from having bought it.")
        return f"{over}. One position failing takes that much of the book with it."

    if since is not None and since < -10 and behind == 0 and ahead >= 3:
        return (f"Down {since:+.0f}% while ahead on every measure shown. The "
                f"market and these numbers disagree — the question is which of "
                f"them knows something the other does not.")

    if since is not None and since < -20 and behind >= ahead:
        return (f"Down {since:+.0f}% and behind on {behind} of {ahead + behind}. "
                f"Nothing on this card argues for it except that you already "
                f"own it.")

    if weeks_below and weeks_below >= 26 and ahead >= 2:
        return (f"{weeks_below:.0f} weeks below its own 200-day average while "
                f"still ahead on {ahead} of {ahead + behind}. Cheap because the "
                f"business changed, or cheap because the price did?")

    if since is not None and since > 100 and weight is not None and weight > 0.10:
        return (f"Up {since:+.0f}% and now {weight * 100:.0f}% of the book. "
                f"Nothing is wrong with it — that is exactly when position size "
                f"stops being an accident and becomes a choice.")

    return ""
