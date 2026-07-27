# AI Portfolio Manager

An **advisory** swing-trading assistant for the **US** and **Turkish (BIST)** markets,
delivered as a personal **Telegram bot**. It recommends buy/sell/hold calls with stop-loss
and target levels — you place the trades yourself in Midas. See [SPEC.md](SPEC.md) for the
full design and the (important) honesty/risk notes.

> ⚠️ **Not financial advice and not a guarantee.** Signals are validated by backtest before
> real money (SPEC §6b). Start with small position sizes. No system reliably beats the market.

## Build status
| Stage | What | Status |
|------|------|--------|
| 1 | Skeleton + Telegram bot + holdings sync (SQLite) | ✅ Done |
| 2 | Market data layer (US + BIST via yfinance, news) | ✅ Done |
| 3 | Deterministic signal engine (technicals + fundamentals + sentiment + macro) | ✅ Done |
| 4 | Backtest + benchmark → **core-satellite** design chosen | ✅ Done |
| 5 | Claude reasoning layer (explanation only) | ✅ Done |
| 6 | Daily digest + 08:30 Europe/Istanbul scheduler | ✅ Done |
| 7 | Offline test suite (169 tests, no network needed) | ✅ Done |
| 8 | Re-validate against an honest benchmark | ✅ Done — **strategy fails the gate** |

**Software is v1 feature-complete. The strategy is not.**

## ⛔ Do not trade this yet — it fails its own validation gate

SPEC §6b says the strategy must beat buy-and-hold before any real money, or the
logic gets revised. Measured against the honest benchmark, it does not.

Backtest 2022-05 → 2026-07. **Equal-weight universe** = buy and hold the same
watchlist, never trading. That is the comparison that matters: the watchlist is a
list of names that are large and successful *today*, so beating a broad index
mostly measures that hindsight pick rather than the timing logic (SPEC §6c).

| | Strategy | Equal-weight universe | Index |
|---|---|---|---|
| **US** return / Sharpe / maxDD | 71.6% / 1.00 / −14.9% | **252.6% / 1.40 / −23.3%** | 85.2% / 0.96 / −18.9% |
| **BIST** return / Sharpe / maxDD | 163.5% / 1.35 / −31.0% | **884.5% / 1.92 / −18.4%** | 470.9% / 1.61 / −22.9% |

The strategy trails the equal-weight universe by **181 points** of total return in
the US and **721 points** in BIST, with a lower Sharpe in both. It is not a
risk-adjusted win either — the only thing it buys is a shallower US drawdown
(−14.9% vs −23.3%), and in BIST even that reverses (−31.0% vs −18.4%).

289 US trades at a 50% win rate and 315 BIST trades at 51% is a coin flip paying
commission on every flip.

**Walk-forward, per calendar year** (70/30 blend vs buy & hold):

| | better/equal Sharpe | kept return | shallower drawdown |
|---|---|---|---|
| US | 3/5 | 4/5 | 3/5 |
| BIST | 2/5 | 2/5 | **5/5** |

In the US the blend helped in 2024 (+4.5%) and 2026 YTD (+2.1%), hurt in 2023
(−3.3%) and 2025 (−1.5%). In BIST it lost return every single year (2022 −11.6%,
2025 −7.1%) while consistently reducing drawdown.

**Read together:** the tactical sleeve is a drawdown-reduction tool that costs a
great deal of return, not an alpha source. The honest summary is that holding the
watchlist would have beaten trading it, in both markets, over this window.

Note this window contains no sustained bear market — the one regime where the
satellite's downside protection is supposed to earn its keep. That is an argument
for testing it on 2008/2020-style data, not for trading it now.

## The selection experiment — what replaced timing, and where it stands

Since holding beat trading, the question changed from *when should I be in?* to
*which names should I hold?* `src/backtest/hold_engine.py` answers the second one:
12-1 momentum, top 8 equal-weighted, quarterly rebalance, **no stops** — being
shaken out is what destroys a hold strategy.

Three claims were tested and two of them died:

1. **875% over the S&P 500, top 8.** Retired. That run used *today's* index
   membership, so every company that fell out of the index over the window was
   missing — which is exactly where momentum's worst outcomes live.
2. **The equal-weight universe cancels the bias.** Wrong for a selector. It holds
   for a static holder, but a momentum rule concentrates into precisely the names
   whose survival was guaranteed by construction.
3. **The edge survives point-in-time membership.** Partly, and unevenly.

`scripts/build_pit_universe.py` reconstructs membership by walking Wikipedia's
change table backwards, recovering 236 names that were in the index and are not
today. Running both legs in that universe (`scripts/hold_pit_compare.py`), 2017–2026:

| | measured |
|---|---|
| survivorship bias | **+36.4 pp/year** — every earlier figure was inflated by roughly this much |
| edge over point-in-time equal-weight | +11.7 pp/year mean, **+6.1 median**, beat it 6/10 years |
| where the edge comes from | 107.5 of 117.2 total points (**~92%**) came from 2024 and 2026 alone |
| the other 8 years | **+1.2 pp/year** — indistinguishable from zero |

The mean/median split and the two-year concentration say the same thing: this is
not a strategy that wins a little most years, it is one that won twice. 2026 — the
single largest contributor at +67.3 — is a partial year, and 2024–2026 is one
semiconductor/AI regime. The losing years are not small either (2019 −13.5, 2023 −9.1).

+36.4 pp is a **lower bound** on the bias: only ~48% of the dropped names can still
be priced, so the rest remain silently excluded even from the point-in-time leg.

**Open question, and the next test:** whether the edge is company selection or one
sector bet wearing its clothes. `scripts/hold_sector_neutral.py` caps the book at
N names per GICS sector and re-runs both legs. If the edge survives the cap, the
sector explanation is excluded; if it collapses, the return was the sector.

About a fifth of the historical universe cannot be sectored — a company delisted
hard enough to lose its ticker also loses its data, and those are precisely the
dropouts point-in-time membership restored. How they are handled would otherwise
decide the answer quietly, so the capped leg runs under all three handlings
(exclude = optimistic, own = middle, shared = pessimistic) and a verdict is only
reported when they agree.

**The entry point, which the rule had backwards.** The goal is to own a company
while it is still climbing. 12-1 momentum does the opposite by construction — it
ranks by what has *already* risen most over a year, so it buys trends at their
most extended. `scripts/hold_early_trend.py` tests the correction: same ranking,
but a name more than N% above its 200-day average is not eligible. Both legs run
with the same sector cap and dates, so one thing differs.

What no leg here can test: whether the company is any good. Every rule reads price
only, and free data carries no usable fundamental history (SPEC §6c). "The next
NVDA" is a claim about a business; this measures the timing half of it.

```bash
python -m scripts.build_pit_universe --check   # membership + price coverage
python -m scripts.build_sector_map             # GICS sector per symbol
python -m scripts.hold_sector_neutral          # does the edge survive the cap?
python -m scripts.hold_early_trend             # does entering earlier help?
```

Nothing here clears the SPEC §6b gate. Ten years, one selector, one market.

## Setup

```bash
# 1. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # Stage 1 needs only a subset; full list is fine

# 2. Configure secrets
cp .env.example .env
#    then edit .env and fill in:
#    - TELEGRAM_BOT_TOKEN   (from @BotFather)
#    - TELEGRAM_OWNER_ID    (from @userinfobot — the bot only talks to this id)
#    - ANTHROPIC_API_KEY    (console.anthropic.com)
#    - FINNHUB_API_KEY      (optional, finnhub.io free tier)

# 3. Verify the data layer (Yahoo changes often; this fails loudly if it broke)
python -m scripts.smoke_data

# 4. Run the tests (offline — no API keys or network needed)
python -m pytest

# 5. Run
python main.py
```

## Validating before you trade
The backtest is the gate SPEC §6b puts in front of real money. Run all three and read
[SPEC §6c](SPEC.md) for what the numbers can and cannot tell you:

```bash
python -m scripts.run_backtest        # strategy vs equal-weight watchlist vs index
python -m scripts.walk_forward        # the same fixed strategy, year by year
python -m scripts.run_core_satellite  # how the 70/30 blend behaves
```

Judge against the **equal-weight watchlist** column, not the index. The watchlist is a
list of names that are large and successful today, so beating an index partly reflects
that hindsight pick rather than the timing logic.

## Using the bot (Stage 1)
Message your bot on Telegram:

| Command | Meaning |
|---------|---------|
| `/start` or `/help` | Show command help |
| `/add US AAPL 10 185.50` | Add/update 10 AAPL at $185.50 average cost |
| `/add BIST THYAO 100 280` | Add/update 100 THYAO at 280 TRY (mapped to `THYAO.IS`) |
| `/remove US AAPL` | Remove a position |
| `/holdings` | List current positions |
| `/value` | Live prices, market value & unrealized P&L |
| `/scan` | Run the signal engine — ranked buy/sell/hold calls with stops, targets, sizing |
| `/digest` | Build today's full morning digest now (valuation + signals) |
| `/log` | Recent recommendations (audit trail) |

Keep this in sync with your real Midas account: update a position after every trade you make.

## Project layout
```
main.py                 # entry point: wires config, storage, data, engine, bot
src/
  config.py             # typed settings loaded from .env
  models.py             # domain types: Holding, Recommendation, Market, Action
  market/               # provider interfaces + Yahoo and Finnhub adapters
  signals/              # indicators -> scoring -> engine -> risk -> formatting
  portfolio/            # live valuation and its rendering
  reasoning/narrator.py # Claude: explanation only, never numbers
  backtest/             # point-in-time simulator + metrics
  storage/db.py         # SQLite: holdings + recommendation audit log
  bot/telegram_bot.py   # owner-locked Telegram handlers + digest scheduler
  util/cache.py         # shared TTL cache
scripts/                # smoke test, backtests, walk-forward, weight search
tests/                  # offline suite; FakeProvider stands in for the market
data/                   # SQLite db (gitignored, created at runtime)
```

## Notes
- **Quant/LLM wall (SPEC §1):** all prices, signals, and levels are computed by Python.
  Claude only *writes the explanation* of those numbers — it never invents data or picks stocks.
  If that layer fails, the digest says so rather than quietly shipping the terse notes.
- **Entries need a pullback.** On a smooth advance RSI pins high and the mean-reversion
  term holds the composite under the buy threshold, so the engine buys dips in uptrends
  rather than breakouts. A quiet `/scan` in a strong bull market is expected behaviour.
- Secrets live in `.env` only and are never committed (`.gitignore` covers it).
