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
| 8 | Re-run validation after the backtest corrections | ⬜ **You** |

**v1 feature-complete.** Remaining before real-money reliance: re-running the
validation (below), and 24/7 hosting on a VPS so the 08:30 digest fires when your
laptop is off.

**Strategy:** core-satellite — ~70% buy-and-hold core + ~30% tactical satellite,
optimising risk-adjusted return (Sharpe, drawdown) rather than raw outperformance,
which the Stage 4 backtest showed was not safely achievable here.

> ⚠️ **The old performance numbers no longer apply.** The backtest was corrected in
> two ways that change results: entries now fill at the next bar's open instead of
> the signal bar's close, and the honest benchmark is an equal-weight hold of the
> same watchlist rather than a broad index (the watchlist is survivorship-biased —
> see [SPEC §6c](SPEC.md)). Re-run `python -m scripts.walk_forward` and
> `python -m scripts.run_backtest`, then put the real figures here.

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
