"""Does the owner's picking actually add anything? The only test of the premise.

Everything else in this system takes one thing on faith: the screen narrows, the
owner chooses well, and the choosing is where the return comes from. That is a
claim, and until now nothing in the repo could check it. A record of purchases
alone cannot — it compares the owner's picks against nothing. What makes the
question answerable is recording the **passes too**: the names that came out of
the same screen, at the same moment, and were declined.

Then the comparison is the right one. Not "did I beat the index" (a different
question, dominated by the market), but "of the twenty names I was shown, did
the eight I chose beat the twelve I did not?" That isolates selection from
everything else, and it is the one thing the owner controls.

The hard part is refusing to answer early. Twelve decisions over eight months
produce a number, and that number is noise: with per-name volatility around 40%
a year, a dozen picks give a standard error near 15 points, so a 10-point lead
is not evidence of anything. This module therefore reports the gap **and** its
standard error, and states plainly when the sample cannot separate skill from
luck yet. SPEC section 6c is the same argument at the portfolio level; this is
it applied to the decisions themselves.

It will also, eventually, be allowed to deliver bad news. That is the point of
building it before there is anything to report.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.market.provider import MarketDataProvider
from src.models import Market

# Below these the module declines to compare. They are not tuned: they are the
# smallest numbers at which the standard error drops near the size of a gap
# worth acting on, and they are deliberately hard to reach quickly.
MIN_PER_GROUP = 10
MIN_DAYS = 365


@dataclass(frozen=True)
class Outcome:
    symbol: str
    market: Market
    action: str
    decided_at: datetime
    reason: str
    entry: float
    now: float

    @property
    def return_pct(self) -> float:
        return (self.now / self.entry - 1.0) * 100

    @property
    def days_held(self) -> int:
        return (datetime.now(timezone.utc) - self.decided_at).days


@dataclass
class Scorecard:
    bought: list[Outcome] = field(default_factory=list)
    passed: list[Outcome] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Enough decisions, aged enough, to say anything at all."""
        if len(self.bought) < MIN_PER_GROUP or len(self.passed) < MIN_PER_GROUP:
            return False
        oldest = min(o.days_held for o in self.bought + self.passed)
        return oldest >= 0 and self.median_age >= MIN_DAYS

    @property
    def median_age(self) -> float:
        ages = [o.days_held for o in self.bought + self.passed]
        return statistics.median(ages) if ages else 0.0

    @property
    def gap_pp(self) -> float | None:
        """Mean return of what was bought minus what was passed on."""
        if not self.bought or not self.passed:
            return None
        return (statistics.fmean(o.return_pct for o in self.bought)
                - statistics.fmean(o.return_pct for o in self.passed))

    @property
    def standard_error(self) -> float | None:
        """How far the gap could move on luck alone.

        Reported next to the gap always, never withheld once the gap looks good.
        A difference smaller than about two of these is not a finding.
        """
        if len(self.bought) < 2 or len(self.passed) < 2:
            return None
        vb = statistics.variance(o.return_pct for o in self.bought)
        vp = statistics.variance(o.return_pct for o in self.passed)
        return (vb / len(self.bought) + vp / len(self.passed)) ** 0.5

    @property
    def verdict(self) -> str:
        """One sentence. Says "cannot tell yet" for as long as that is true."""
        if not self.bought and not self.passed:
            return ("Nothing recorded yet. /pass <SYMBOL> <why> on the names you "
                    "look at and decline — without those, nothing here can ever "
                    "be measured.")
        counts = (f"{len(self.bought)} bought, {len(self.passed)} passed, "
                  f"median age {self.median_age:.0f} days")
        if not self.ready:
            need = []
            if len(self.bought) < MIN_PER_GROUP:
                need.append(f"{MIN_PER_GROUP - len(self.bought)} more purchases")
            if len(self.passed) < MIN_PER_GROUP:
                need.append(f"{MIN_PER_GROUP - len(self.passed)} more passes")
            if self.median_age < MIN_DAYS:
                need.append(f"{MIN_DAYS - self.median_age:.0f} more days of age")
            return (f"{counts}. Not enough to separate skill from luck — "
                    f"needs {', '.join(need)}. The number would exist; it would "
                    f"not mean anything.")

        gap, se = self.gap_pp, self.standard_error
        if gap is None or se is None:
            return f"{counts}. Cannot compute a gap."
        if abs(gap) < 2 * se:
            return (f"{counts}. Your picks are {gap:+.1f} pp against the ones you "
                    f"declined, with a standard error of {se:.1f} pp. That is "
                    f"inside the noise: it is not yet evidence either way.")
        direction = "ahead of" if gap > 0 else "behind"
        return (f"{counts}. Your picks are {gap:+.1f} pp {direction} the ones you "
                f"declined (standard error {se:.1f} pp) — larger than luck "
                f"comfortably explains. This is the first real evidence about "
                f"the part of the system that is you.")


def build_scorecard(store, provider: MarketDataProvider,
                    since: datetime | None = None) -> Scorecard:
    """Price every recorded decision as it stands today.

    A decision whose price cannot be read is listed, never dropped silently:
    quietly discarding the unreadable ones would bias the comparison towards
    whichever side happens to still be quoted.
    """
    card = Scorecard()
    since = since or datetime.now(timezone.utc) - timedelta(days=365 * 10)

    for row in store.decisions(since=since):
        entry = row["price"]
        market = Market(row["market"])
        if not entry:
            card.unpriced.append(f"{row['symbol']} (no price recorded)")
            continue
        try:
            quote = provider.get_quote(row["symbol"], market)
        except Exception:  # noqa: BLE001
            quote = None
        if quote is None or not quote.price:
            card.unpriced.append(f"{row['symbol']} (not quoted today)")
            continue

        outcome = Outcome(
            symbol=row["symbol"], market=market, action=row["action"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            reason=row["reason"], entry=float(entry), now=float(quote.price),
        )
        (card.bought if outcome.action == "BOUGHT" else card.passed).append(outcome)

    return card
