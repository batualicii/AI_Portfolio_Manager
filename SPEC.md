# AI Portfolio Manager — Project Specification

_Last updated: 2026-06-26_

## Goal
A personal **advisory** bot that manages a US + BIST (Turkish) stock portfolio with a
**core-satellite** design (see §0), delivering a **daily digest** of tactical buy/sell
ideas + risk alerts with stop-loss and target levels. It recommends — the user executes
manually in Midas.

## 0. Strategy design — CORE-SATELLITE (revised after Stage 4 backtest)
The Stage 4 backtest proved that **beating buy-and-hold on raw return is not safely
achievable** here (2022–2026 had no sustained bear market for these indices; long-only
tactical trading mostly just misses upside). Forcing it via concentration produced
unacceptable risk. So the design was revised to what the evidence supports:
- **Core (~70%)** — buy-and-hold the user's quality holdings / index exposure. Captures
  the bull, which is the hard-to-beat part.
- **Satellite (~30%)** — the tactical signal engine: high-conviction swing entries, each
  with a mandatory stop, plus **risk alerts** on core holdings.
- **Objective:** best **risk-adjusted** return (Sharpe / drawdown), not raw outperformance.
  Validated: a 70/30 blend kept ~99% (US) / ~93% (BIST) of buy-and-hold return with
  **better Sharpe and shallower drawdowns in both markets**.
- Still **in-sample**; needs walk-forward validation, and the satellite's downside
  protection will matter most in a bear market not present in the test window.

## 1. Core behavior
- **Advisory only** — recommends, never auto-trades. User executes in Midas manually.
- **Swing-trading** horizon (days to weeks).
- **Aggressive** risk appetite.
- **Long-only, no leverage, no shorting** (matches Midas retail reality).
- AI decides: basket size, US-vs-BIST split, FX weighting, per-trade position sizing.
- **Objective revised (see §0): best risk-adjusted return via core-satellite**, not raw
  absolute return — the Stage 4 backtest showed the latter isn't safely achievable here.

## 2. Decision engine (signal inputs)
- **Technical:** momentum, moving averages, RSI / MACD, support/resistance, volume.
- **Fundamentals:** earnings, growth, valuation, balance-sheet quality.
- **News & sentiment:** earnings dates, headlines, analyst actions.
- **Macro & FX:** rates, inflation, USD/TRY, sector rotation.
- **Risk management:** AI sets stop-loss, take-profit, and position size on **every**
  trade. Hard cap on any single position. Mandatory stop on every trade so a single
  bad swing can't wreck the book.
- **Objective:** maximize absolute return *within* the aggressive risk caps.

### CRITICAL architectural rule — quant/LLM separation
- **Deterministic Python code computes ALL signals, prices, and levels.** The numbers are
  never produced by the LLM.
- **Claude only writes the human-readable explanation** of what the code already decided.
  It must never invent prices, pick stocks on its own, or override the quantitative output.
- This wall prevents confident-but-wrong LLM hallucinations from driving real trades.

## 3. Data stack
- **US:** Yahoo Finance (`yfinance`) for prices/fundamentals + Finnhub / Alpha Vantage
  free tier for news/earnings.
- **BIST:** Yahoo Finance (`.IS` tickers, e.g. `THYAO.IS`) to start. Add a paid Turkish
  source (İş Yatırım / Matriks / Finnet) later **only if** BIST data quality proves too thin.
- **Strategy:** start **100% free**, validate the whole system, then upgrade BIST if needed.

## 4. Interface & flow
- **Telegram bot** (free, fast to build).
- User imports current holdings + cost basis once; updates after each manual trade.
- **Daily digest** each morning: current positions, P&L, and ranked buy/sell/hold
  actions with reasons + price levels (entry / stop / target).
- On-demand Q&A ("should I sell X?") is a **v2 nice-to-have**; v1 is the daily digest.

## 5. Tech / hosting
- Python service running 24/7 on a small cloud VPS.
- Scheduler (cron) triggers the daily analysis + digest each morning automatically.
- User codes, so a bit of setup/maintenance is acceptable.

## 6. Constraints
- No sector or ethical (e.g. halal) exclusions.
- Broker: **Midas** for both US and BIST — no public API, so the bot is kept in sync
  **manually** (user reports holdings and executed trades).

## Tooling (skills & plugins)
**Installed skills**
- `telegram-bot-builder` — Telegram Bot API, architecture, UX, scaling. Powers delivery.
- `python-design-patterns` (wshobson/agents) — clean layering of bot/data/signal/reasoning code.
- `finance-sentiment` (himself65/finance-skills) — news & market sentiment analysis for the
  sentiment signal input.

**Available locally (no install)**
- `claude-api` — reference for the Claude API; powers the reasoning/narrative layer that turns
  quantitative signals into human-readable "why buy/sell" explanations.
- `deep-research` — deeper fundamental/macro research on specific names when needed.
- `/code-review`, `/simplify` — code quality as the codebase grows.

**Data store**
- The `/plugin` command is unavailable in this (VSCode) environment, so `duckdb-skills` cannot
  be installed as a plugin. Not a blocker — DuckDB ships as a plain Python library (`pip install
  duckdb`) added at build time. For v1, **SQLite (Python built-in)** is sufficient for the
  recommendation log + price cache; DuckDB is an optional upgrade for faster analytical queries.

**Deferred plugins (only if project grows)**
- `mcp-server-dev` — expose the portfolio engine as an MCP server (live tool) later.
- `postman` — API testing for data endpoints (marginal).

**Python libraries (added at build time, not Claude plugins)**
- `python-telegram-bot`, `yfinance`, `requests`/`httpx`, `pandas`, `pandas-ta` (technicals),
  `anthropic` (reasoning layer), `APScheduler`/cron (daily trigger).

## 6b. Validation & safety (MANDATORY before real-money trading)
- **Backtest gate:** the strategy must be backtested on 2–5 years of history for BOTH markets
  before any live use. If it does not beat a simple buy-and-hold benchmark (S&P 500 / BIST 100),
  the logic is revised before trading. No untested signals reach real money.
- Paper-trading period was considered and **deliberately skipped** by the user. Consequence:
  the first real-money weeks are effectively the live test → **start with small position sizes**
  until several weeks of real recommendations have been observed.
- Both markets (US + BIST) are built from the start, accepting that **BIST signal quality is
  lower** initially (thinner data, sparser news, English-first sentiment tooling).

## 7. Out of scope (v1)
- Auto-execution / broker integration.
- Shorting, leverage, options.
- On-demand chat (v2).
- (Backtesting is now IN scope — see section 6b.)

## Accepted structural reality
Midas has no public API, so the loop is semi-manual:
1. User tells the bot current holdings + cost basis (and updates after trades).
2. Bot pulls market data, runs analysis, sends the daily digest with levels.
3. User places trades in Midas and reports what was executed.

---

## Open items for the build phase (not yet decided)
- ~~Morning send time / timezone~~ → **DECIDED: 08:30 every day, Europe/Istanbul (UTC+3, no DST).**
  Lands before BIST open (10:00) and captures overnight US close + pre-market.
- Which VPS / hosting provider.
- Ranking logic weighting between technical vs fundamental vs sentiment signals.
- How the AI reasoning is powered (e.g. Claude API for the narrative/analysis layer
  on top of quantitative signals).
- Whether to log every recommendation for later performance review (recommended).
