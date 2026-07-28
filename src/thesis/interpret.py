"""Say what a number means, without saying whether it is good.

The first version of `/brief` printed "revenue growth 31%, margin 12%, P/E 24"
and stopped. That is unreadable unless you already know what those numbers look
like elsewhere — which is the knowledge the page was supposed to supply.

So each metric now comes with two things:

  * **what it measures**, in one plain sentence
  * **where this company sits** against its own sector's median, when that has
    been measured (`scripts/build_sector_stats.py`)

What it deliberately does not do is grade. "P/E 24 is expensive" is a forecast
wearing a fact's clothes — it assumes growth will not arrive. "P/E 24, the median
in its sector is 18" is an observation, and the reader can decide what to make of
it. That distinction is the same one SPEC section 1 draws around the LLM, applied
to the numbers themselves.

The checklist at the end counts conditions met. It is a **description of today**,
not a prediction: this repo measured that a composite score cannot be shown to
predict anything at this portfolio size (SPEC section 6c), and a number between 0
and 10 invites exactly the confidence that measurement denies.

Two limits on the peer comparison are carried in the output rather than left for
the reader to remember:

  * **A median is only as good as its sample.** Financials has 114 usable names;
    Utilities has 11. "Above the median" means very different things in those two
    sentences, so every comparison states its `n`, and a thin one says so.
  * **Comparisons are within a sector, never across one.** A 24% profit margin is
    ordinary for a bank and remarkable for a retailer, because the two are not
    measuring the same thing. These lines place a company against its peers; they
    cannot rank sectors, and the page says so.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from src.market.types import Fundamentals
from src.models import Market

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
STATS = ROOT / "universes" / "sector_stats.json"

# Above this an "above/below the median" statement is worth reading straight;
# below it the median still beats nothing, but it moves if two names change.
THIN_SAMPLE = 25

WITHIN_SECTOR_ONLY = (
    "Every comparison above is against this company's own sector and nothing "
    "else. A 24% margin is ordinary for a bank and remarkable for a retailer, "
    "so these lines place a company among its peers — they cannot rank one "
    "sector against another."
)


MEANINGS = {
    "revenue_growth": "how much faster the company is selling than a year ago",
    "profit_margin": "how much of each lira of sales it actually keeps",
    "pe_ratio": "years of today's earnings you are paying for one share",
    "forward_pe": "the same, against what analysts expect next year",
    "beta": "how much it moves when the whole market moves",
    "held_pct_institutions": "how much of it funds already own",
}


@dataclass(frozen=True)
class Reading:
    """One metric, its meaning, and where it stands — never a grade."""
    label: str
    value: float
    display: str
    meaning: str
    peer_median: float | None = None
    percentile: int | None = None
    sample_size: int | None = None

    @property
    def thin(self) -> bool:
        """True when the median rests on too few peers to lean on."""
        return self.sample_size is not None and self.sample_size < THIN_SAMPLE

    @property
    def context(self) -> str:
        if self.peer_median is None:
            return "no sector comparison available"
        direction = "above" if self.value > self.peer_median else "below"
        med = (f"{self.peer_median * 100:.0f}%"
               if abs(self.peer_median) < 10 else f"{self.peer_median:.0f}")
        line = f"sector median {med} — this is {direction} it"
        if self.percentile is not None:
            line += f", higher than {self.percentile}% of its sector"
        if self.sample_size is not None:
            # Without n the reader cannot tell a median of 114 names from a
            # median of 11, and both print the same confident sentence.
            line += f" (n={self.sample_size}"
            line += ", thin — read the direction loosely)" if self.thin else ")"
        return line


def _load_stats() -> dict:
    if not STATS.exists():
        return {}
    try:
        return json.loads(STATS.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def peer_coverage(sector: str | None, market: Market | None = None) -> str | None:
    """Why this name has no peer context, when it has none.

    A blank where a comparison should be reads as "nothing notable", which is a
    different claim from "we never measured this". BIST is the case that matters:
    101 names across ~34 sectors is ~3 per sector, so no amount of re-running
    produces a median. That is a property of the market's size, not a failed job,
    and saying so stops the same question being asked every few months.
    """
    stats = _load_stats()
    if not stats:
        return ("Sector medians have not been built yet — "
                "`python -m scripts.build_sector_stats` measures them.")

    built_for = stats.get("market")
    if market is not None and built_for and built_for != market.value:
        if market is Market.BIST:
            return (
                "No sector medians for BIST. The index is ~100 names spread over "
                "~34 sectors — about three each, where a median needs at least "
                f"{stats.get('min_sample', 8)}. This is a limit of the market's "
                "size, not a missing run, and the US medians are not a stand-in: "
                "different economy, different cost of capital, different normal."
            )
        return (f"Sector medians here were measured on {built_for} names, so "
                f"they are not a fair reference for a {market.value} company.")

    if sector and sector in stats.get("omitted", {}):
        counts = stats["omitted"][sector] or {}
        worst = max(counts.values()) if counts else 0
        return (f"{sector} had only {worst} usable names in this universe, under "
                f"the {stats.get('min_sample', 8)} needed before a median says "
                "anything. Its numbers are left uncompared rather than compared "
                "badly.")

    if not sector:
        return "The data source did not report a sector, so there is no peer set."
    return None


def read_fundamentals(f: Fundamentals, sector: str | None = None) -> list[Reading]:
    """Turn raw fields into readable observations with peer context."""
    stats = _load_stats().get("sectors", {}).get(sector or "", {})
    out: list[Reading] = []

    for field, pct in (("revenue_growth", True), ("profit_margin", True),
                       ("pe_ratio", False), ("beta", False)):
        value = getattr(f, field, None)
        if value is None:
            continue
        peers = stats.get(field, {})
        median = peers.get("median")
        percentile = None
        if peers.get("values"):
            below = sum(1 for v in peers["values"] if v < value)
            percentile = int(below / len(peers["values"]) * 100)
        out.append(Reading(
            label=field.replace("_", " "),
            value=value,
            display=f"{value * 100:.0f}%" if pct else f"{value:.1f}",
            meaning=MEANINGS.get(field, ""),
            peer_median=median,
            percentile=percentile,
            sample_size=peers.get("n") if median is not None else None,
        ))
    return out


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool | None      # None = could not be evaluated
    detail: str


def quality_checklist(f: Fundamentals, price_ctx=None) -> list[Check]:
    """Conditions met today. A description, explicitly not a forecast.

    Every item is a fact about the present that a human would otherwise have to
    look up one at a time. None of them, alone or added together, has been shown
    to predict a return — which is why this returns a list of statements rather
    than a number.
    """
    checks: list[Check] = []

    def add(name: str, value, test, detail_yes: str, detail_no: str) -> None:
        if value is None:
            checks.append(Check(name, None, "not reported by the data source"))
        else:
            ok = test(value)
            checks.append(Check(name, ok, detail_yes if ok else detail_no))

    add("Growing", f.revenue_growth, lambda v: v > 0.05,
        "revenue is rising meaningfully", "revenue is flat or shrinking")
    add("Profitable", f.profit_margin, lambda v: v > 0.0,
        "it keeps money from what it sells", "it loses money on what it sells")
    add("Earnings priced sanely", f.pe_ratio, lambda v: 0 < v < 40,
        "not priced for perfection", "priced for a lot going right")

    if price_ctx is not None:
        if price_ctx.vs_200d_pct is not None:
            checks.append(Check(
                "In an uptrend", price_ctx.vs_200d_pct > 0,
                f"{price_ctx.vs_200d_pct:+.0f}% against its 200-day average",
            ))
        if price_ctx.worst_drawdown_pct is not None:
            survivable = price_ctx.worst_drawdown_pct > -60
            checks.append(Check(
                "Falls you could sit through", survivable,
                f"worst fall {price_ctx.worst_drawdown_pct:.0f}% — "
                + ("hard but survivable at a sane weight" if survivable
                   else "the kind of fall that ends most people's patience"),
            ))

    return checks


def checklist_line(checks: list[Check]) -> str:
    passed = sum(1 for c in checks if c.passed is True)
    unknown = sum(1 for c in checks if c.passed is None)
    total = len(checks) - unknown
    line = f"meets {passed} of {total} checks"
    if unknown:
        line += f", {unknown} unanswerable"
    return line
