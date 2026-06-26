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
| 4 | Backtest + benchmark → **core-satellite** design validated | ✅ Done |
| 5 | Claude reasoning layer (explanation only) | ✅ Done |
| 6 | Daily digest + 08:30 Europe/Istanbul scheduler | ✅ Done |

**v1 feature-complete.** Remaining before real-money reliance: walk-forward (out-of-sample)
validation, and 24/7 hosting on a VPS so the 08:30 digest fires when your laptop is off.

**Strategy (validated):** core-satellite — ~70% buy-and-hold core + ~30% tactical satellite.
Beats buy-and-hold on **risk-adjusted** return (Sharpe, drawdown) in both markets; see
`scripts/run_core_satellite.py`. Raw outperformance was shown to be unachievable safely.

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

# 3. Run
python main.py
```

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
main.py                 # entry point (runs the bot; scheduler added later)
src/
  config.py             # typed settings loaded from .env
  models.py             # domain types: Holding, Recommendation, Market, Action
  storage/db.py         # SQLite: holdings + append-only recommendation log
  bot/telegram_bot.py   # owner-locked Telegram handlers
data/                   # SQLite db (gitignored, created at runtime)
```

## Notes
- **Quant/LLM wall (SPEC §1):** all prices, signals, and levels are computed by Python.
  Claude only *writes the explanation* of those numbers — it never invents data or picks stocks.
- Secrets live in `.env` only and are never committed (`.gitignore` covers it).
