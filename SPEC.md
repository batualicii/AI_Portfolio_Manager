# AI Portfolio Manager — Project Specification

_Last updated: 2026-07-27_

## Goal
A personal **advisory** bot for a US + BIST (Turkish) portfolio. It does **not** generate
buy/sell signals. It records why the user owns each position, watches for the specific
conditions that would prove that reasoning wrong, and enforces the position limits and
trading pace the user set for themselves while calm. The user researches and executes
manually in Midas.

## 0. Strategy design — THESIS TRACKING (revised 2026-07, after the selection engine failed)

### What was tried, and what it measured
Two generations of signal engine were built and validated honestly. Neither cleared §6b:

| Design | Result against the honest benchmark |
|---|---|
| Tactical timing (buy/sell/trim) | −181 pts (US) / −721 pts (BIST) vs equal-weight hold |
| 12-1 momentum selection, 8 names | +6.6 pp/yr, but **−0.5 pp/yr over 2017–2023** — all of it from 2024–2026 |
| Early-entry filter (25% / 15% above 200d) | Does not create edge; only changes which regime it works in |
| All four, risk-adjusted | **All four lose to passive equal-weight on Sharpe** (0.98–1.12 vs 1.41) |

### The finding that ended the search
The annual edge of an 8-name portfolio has a standard deviation of **13.3 pp**. The
+6.6 pp/yr measured over ten years therefore carries a standard error of 4.2 —
statistically indistinguishable from zero. Confirming it would take roughly **32 years**
of data; confirming a 3 pp edge would take **~154 years**.

This is not a data shortage that more history fixes. It is a mathematical ceiling set by
how few positions a small account can hold. **No backtest this project can run will ever
establish that a rule of this kind works.** Continuing to tune signals against ten years
of one market is, at this portfolio size, a way of fitting noise.

### What replaced it
A small investor's real advantages are not statistical, and none of them were being used:

| Advantage | Why it is real | Usable in code |
|---|---|---|
| **Capacity** | A large fund cannot take a meaningful position in a small company — arithmetic, not policy. That pond is left to individuals | Yes — universe |
| **No career risk** | A manager underperforming for two years is fired; the user is not, and can hold through a 50% drawdown | No — discipline |
| **No redemption pressure** | Funds must stay liquid for daily redemptions; the user need not | Partly |
| **Concentration allowed** | Diversification rules cap funds at a few percent per name | Partly |
| **Domain knowledge** | Turkish companies, consumers and regulation are better understood locally than from abroad | No — human |

Four of the five are behaviour, not signal. So the system's job changed accordingly:

- **Core** — broad index/fund exposure. This is where wealth actually compounds, and it
  requires no skill. Not exciting; it is the base case that funds everything else.
- **Concentrated sleeve** — a small number of positions, each with a written thesis, held
  for years. **Its purpose is not to beat the index.** Its purpose is exposure to outcomes
  an index cannot produce: an index cannot 10x a position, a five-name sleeve can. The
  reason to hold it is asymmetry, not expected value.
- **The bot's role** — not to pick, but to make the user's own advantages usable: record
  the thesis, check its falsifiers mechanically, enforce sizing, and slow down trading.

### Why quarterly rotation had to go
The previous rule made the intended outcome structurally impossible. Quarterly 12-1
momentum would have sold NVDA in early 2019 (after −56%) and again in early 2023
(after −66%), capturing perhaps a third of the move. Momentum rotation exits precisely
the drawdowns a position must survive to compound into something large.

> ⛔ **Nothing here is validated for real money.** The measured results above stand; they
> are why the design changed. The new design is not "a better strategy that beats the
> index" — it is an honest structure for taking a deliberate, survivable risk.

## 1. Core behavior
- **Advisory only** — never auto-trades, and now does not recommend either. The user
  decides what to own; the system records and monitors that decision.
- **Multi-year** holding horizon. The previous swing horizon (days to weeks) was the
  source of the trading costs and the premature exits documented in §0.
- **Aggressive** risk appetite, expressed as concentration rather than as turnover.
- **Long-only, no leverage, no shorting** (matches Midas retail reality).
- **Objective (see §0):** exposure to asymmetric outcomes, inside limits that make being
  wrong survivable. Explicitly **not** "beat the index" — that claim is unmeasurable at
  this portfolio size and pretending otherwise is how the last two designs went wrong.

## 2. What the system actually computes

It no longer ranks or recommends. Three jobs:

**a. Thesis record.** For every position: why it is owned, in the user's own words, with a
conviction level and a review date. Written at purchase, when there is no pressure.

**b. Falsifiers.** Each thesis carries at least one *checkable* condition that would prove
it wrong — trend break, drawdown from the post-entry high, revenue growth or margin below
a threshold. These are written while calm and checked mechanically later, when the user is
not. That asymmetry is the whole point of the mechanism.

Every alert carries the observed number, not an adjective: *"revenue growth 31% → 12%,
threshold 20%"*, never *"fundamentals deteriorating"*.

**c. Guardrails.** Position ceilings by conviction, concentration limits per name/sector/
market, trading-pace reporting, and a flag on any sale where **no falsifier fired** — the
question being *did the thesis break, or did the price just move?* Barber & Odean's
finding is the reason this exists: individual investors' returns fall the more they trade.

Technicals, fundamentals, news and macro are still fetched — but as **evidence for a
falsifier**, never as a reason to buy or sell on their own.

### CRITICAL architectural rule — quant/LLM separation
- **Deterministic Python code computes ALL numbers**: prices, thresholds, whether a
  falsifier fired, position weights. The LLM produces none of them.
- **Claude's role, restated for this design:** help the user articulate and edit *their
  own* thesis, and turn a fired falsifier into a plain sentence. It must never author a
  thesis, propose a position, invent a threshold, or forecast.
- The wall matters more here, not less: the system now speaks in the user's own reasoning,
  and a fabricated sentence would be indistinguishable from their own conviction.

## 3. Data stack
- **US:** Yahoo Finance (`yfinance`) for prices/fundamentals + Finnhub / Alpha Vantage
  free tier for news/earnings.
- **BIST:** Yahoo Finance (`.IS` tickers, e.g. `THYAO.IS`) to start. Add a paid Turkish
  source (İş Yatırım / Matriks / Finnet) later **only if** BIST data quality proves too thin.
- **Strategy:** start **100% free**, validate the whole system, then upgrade BIST if needed.

## 4. Interface & flow
- **Telegram bot** (free, fast to build).
- User imports current holdings + cost basis once; updates after each manual trade.
- **Weekly summary**, not daily: positions, open theses, reviews now due, and the
  quarter's trade count. A daily price message is itself a trading trigger, and on a
  multi-year horizon there is nothing new to say most mornings.
- **Immediate alert** whenever a falsifier fires — the one event that genuinely cannot
  wait, and the only thing that interrupts the user.
- **Research pool** (`/pool`): the screen's reading list, scanned weekly in the
  background, served instantly from storage, and delivered unprompted only on the 1st
  of the month with new entrants marked. Weekly delivery was considered and rejected —
  twenty fresh names every week is a trade generator, and Barber & Odean is the one
  finding here that does not depend on this repo's own measurements.
- **One renderer for holdings and candidates** (`src/thesis/card.py`). Both sides show
  the same four facts against the same references in the same layout. This is a
  behavioural constraint, not a formatting preference: described more generously than
  what the user already owns, a candidate always looks better, and the resulting
  churn is precisely what section 0 was rewritten to avoid. A test asserts the two
  renders are byte-identical for identical inputs.
- No ranked buy/sell list, and `/positions` never says sell. Grouping positions by
  *what changed* is a statement of fact; grouping them by *what to do* would reinstate
  the quarterly rotation rule (section 0) through the interface.

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

> ⚠️ `finance-sentiment` (himself65/finance-skills) was listed here as installed. It never
> was — the sentiment input comes from the Finnhub API, not from that plugin. Checked
> 2026-07: no finance or investing skill is enabled in this project. The strategy work in
> this repo rests on the model's own domain knowledge and on the measurements in §6c, not
> on any packaged finance tooling.

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

## 6c. Known limits of the backtest (read before believing any number it prints)

- **The ceiling: at this portfolio size, no backtest here can establish an edge.** Read
  this first, because it bounds everything below it. The annual edge of an 8-name book
  has a standard deviation of **13.3 pp** (measured, 2017–2026). Over ten years that is a
  standard error of 4.2, so the +6.6 pp/yr that was measured is **not distinguishable
  from zero**. Detecting a 3 pp edge at 95% confidence would take ~154 years; confirming
  the 6.6 would take ~32. Widening the basket helps but not enough — 50 names still needs
  ~31 years for 3 pp. **A future session that starts tuning parameters against this data
  set is repeating a mistake this project already made twice.** The backtests that remain
  are for understanding mechanism and cost, not for proving profitability.

Every backtest result carries these caveats too. They are not reasons to distrust the
design; they are the boundaries of what the evidence actually covers.

- **Survivorship bias in the watchlist — the biggest one.** `SignalConfig.us_universe`
  and `bist_universe` are hand-picked lists of companies that are large and successful
  *today* (NVDA, AVGO, ASTOR, FROTO…). Backtesting a strategy that trades those names
  against a broad index credits a stock-selection decision made with hindsight to the
  timing logic. **This is why every script now also reports an equal-weight buy-and-hold
  of the same watchlist**: that reference holds the bias constant, so the gap between the
  strategy and the equal-weight universe is timing, while the gap to the index is mostly
  selection. Judge the strategy on the former.
- **The shared universe does not cancel the bias for a *selection* strategy.** The bullet
  above holds for the tactical engine, which trades a fixed list: bias hits both sides
  roughly equally. It does **not** hold for `hold_engine.py`, which ranks and concentrates —
  a static equal-weight holder is barely affected, while a momentum selector loads up on
  exactly the names whose survival was guaranteed by construction. Removing it requires
  point-in-time membership (`scripts/research/build_pit_universe.py` → `HoldBacktester(members_at=…)`),
  not a shared universe. **Measured, 2017–2026: the bias was worth +36.4 pp/year**, and that
  is a lower bound because only ~48% of dropped names can still be priced. Any figure in this
  repo produced without `members_at` is inflated by roughly that much and must not be quoted.
- **An edge concentrated in two years is not an edge yet.** After removing the bias, the
  selection strategy beat point-in-time equal-weight by +11.7 pp/year on average — but ~92%
  of the total came from 2024 and 2026, both inside one semiconductor/AI regime, and 2026 is
  a partial year. The remaining eight years average +1.2 pp. Mean well above median is the
  signature of a couple of large wins, not of repeatable skill. Before this counts as
  evidence, the sector explanation has to be excluded (`scripts/research/hold_sector_neutral.py`).
- **No historical fundamentals or news.** Free history does not exist for either, so the
  fundamental and sentiment sub-scores are inert in every backtest. Only the
  technical + macro backbone — 70% of the live weight — is ever validated.
- **Parameters were selected while looking at the test window.** `optimize_weights.py`
  splits train/test honestly, but a human chose the shipped combination after seeing
  both columns, which makes the test window partly in-sample. Treat the walk-forward
  consistency counts, not the headline Sharpe, as the real evidence.
- **One market regime.** The 2022–2026 window has no sustained bear market for these
  indices. The satellite's whole purpose is downside protection, and that is precisely
  what the data cannot test.
- **Fills are optimistic even after the fix.** Entries fill at the next bar's open with
  a 0.1% commission-and-slippage charge. Real slippage on a gap open, and BIST liquidity
  in particular, can be worse.

## 6b. Validation & safety (MANDATORY before real-money trading)

### The old gate, and why it was retired
The original gate was "beat buy-and-hold in a backtest". Two designs were measured
against it honestly and both failed (§0). More importantly, §6c now shows the gate is
**unmeasurable at this portfolio size** — an 8-name book would need ~32 years of data to
confirm even a large edge. A gate that cannot be evaluated is not a safeguard; it is a
place to hide a wish. It is replaced.

### The gate that applies now
The concentrated sleeve is a deliberate bet, so the gate is about **survivability and
discipline**, both of which are measurable this week:

1. **No position exceeds its conviction ceiling.** A single mistake must not be fatal.
2. **Every position has a written thesis and at least one *checkable* falsifier**,
   recorded at purchase. No thesis, no position.
3. **Every sale is explained.** A sale with no falsifier fired is logged as unexplained.
   The count of those is the honest measure of whether this is being followed.
4. **Trading pace is reported every quarter.** Rising turnover on a multi-year horizon is
   the failure mode, and it is visible before it is expensive.
5. **The core is not touched by any of this.** It is index exposure and it stays put.

These are checks on behaviour, not forecasts of return — which is the point. Nothing
above claims the sleeve will beat anything, and the system must never imply it will.
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
2. Bot pulls market data, checks each thesis's falsifiers, and sends the weekly summary
   — or interrupts immediately if one fired.
3. User places trades in Midas and reports what was executed, **and why** — a sale with
   no falsifier behind it is recorded as unexplained (§6b), because that count is the
   only honest measure of whether the discipline is being kept.

---

## Open items for the build phase (not yet decided)
- ~~Morning send time / timezone~~ → **DECIDED: Europe/Istanbul (UTC+3, no DST).** The
  weekly summary keeps that slot; the falsifier check runs daily but stays silent unless
  something fires.
- ~~Ranking weights between technical / fundamental / sentiment~~ → **CLOSED.** There is
  no ranking any more; §0 explains why tuning those weights was measuring noise.
- ~~Whether to log every recommendation~~ → **DECIDED: yes**, and now extended — thesis
  openings, falsifier firings and closing reasons are all append-only (§6b).
- Which VPS / hosting provider.
- Position ceilings per conviction level — the numbers in §6b gate 1 are still to be set.
