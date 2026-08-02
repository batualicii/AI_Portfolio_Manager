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
    if fundamentals.beta is not None:
        card.also.append(f"beta {fundamentals.beta:.1f}")
    if institutional is not None:
        card.also.append(f"institutions {institutional * 100:.0f}%")

    if not medians:
        card.warnings.append(
            "no medians for this market at all — compared against fixed lines, "
            "which is the weakest reference here"
        )
    elif reference_kind == "market":
        card.warnings.append(
            f"no peer sample in {sector} — compared against the whole market, "
            f"which mixes industries: read it as a level, not as "
            f"\"better than its peers\""
        )
    if thesis_summary is None and weight is not None:
        card.warnings.append(
            "no thesis on record — nothing can be monitored, nothing will alert you"
        )
    if crowded_sector_weight:
        card.warnings.append(
            f"you already hold {crowded_sector_weight * 100:.0f}% in {sector}"
        )

    return card
