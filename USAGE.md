# Running it

Practical guide. For *why* the system works this way, read [SPEC §0](SPEC.md); for
the evidence behind it, [README](README.md).

One sentence of context, because it changes what you expect: **this bot does not
tell you what to buy.** It narrows the universe, assembles the research, records
why you bought, and then tells you when something you wrote down comes true. Two
generations of signal engine were built here and measured honestly; neither
worked, and SPEC §6c shows why no backtest at this portfolio size could ever
prove one did.

---

## 1. Install

```bash
git clone <your repo url>
cd AI_Portfolio_Manager
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | How to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | message [@BotFather](https://t.me/BotFather), `/newbot` |
| `TELEGRAM_OWNER_ID` | **yes** | message [@userinfobot](https://t.me/userinfobot), copy the number |
| `ANTHROPIC_API_KEY` | no | [console.anthropic.com](https://console.anthropic.com) — only affects `/draft` wording |
| `FINNHUB_API_KEY` | no | [finnhub.io](https://finnhub.io) free tier — only affects headlines in `/brief` |

The owner id matters: the bot ignores every other Telegram account. It is a
single-user tool holding your positions.

**Never commit `.env`.** It is gitignored; keep it that way.

## 3. Check the data layer works

Yahoo changes its API without warning, so confirm it before relying on it:

```bash
python -m scripts.smoke_data
```

Prices, history, fundamentals and USD/TRY for one US name, one BIST name. If this
fails, nothing downstream will work and the failure will be harder to read there.

## 4. Build the universes

These are the ponds the screen fishes in. Run once, refresh every few months:

```bash
python -m scripts.build_smallcap_universe   # S&P 600 — ~600 US small caps
python -m scripts.build_bist_universe       # BIST 100 — ~100 Turkish names
python -m scripts.build_sector_stats                                  # US
python -m scripts.build_sector_stats --universe universes/bist.json --market BIST
```

The last two are slow (one call per name) and make the difference between
`/brief` printing "P/E 24" and printing "P/E 24, the median in its sector is 18".
Re-run them a few times a year. Each market writes its own file, so neither run
overwrites the other.

## 5. Run

```bash
python main.py
```

Then message your bot on Telegram. `/start` lists every command.

To keep it running after you close the terminal, use a VPS with `systemd`, or
`tmux`/`screen` on a machine that stays on. The weekly summary and the daily
falsifier check only fire while the process is alive.

---

## The loop

### Seeing where you stand

```
/positions
```

Every holding in one message, grouped by what has actually changed:

```
🧾 Positions — 04 Aug 2026
9 positions · 108,557 TRY · top two 46% · Info Technology 32% · 0/9 with a thesis

── Nothing has broken (5) ──

BIMAS · Consumer Staples · 11.4% of the book · since entry +23%
    3/4 ahead
    ahead  growth +31% (market +42%, n=96) · trend +9% (its own 200-day average)
    also   12m +18% · worst fall −27%
    ⚠️ no peer sample in Consumer Staples — compared against the whole market
    ⚠️ no thesis on record — nothing can be monitored, nothing will alert you

── Something changed (3) ──

SWKS · Info Technology · 7.2% of the book · since entry −14%
    1/4 ahead
    ahead  margin +9% (sector +4%, n=64)
    behind growth +4% (sector +8%, n=64) · P/E 41 (sector 39, n=64) ·
           trend −8% (its own 200-day average, 6 weeks below)
    also   12m −21% · worst fall −52%
```

A position lands in **Something changed** when a falsifier you wrote has fired,
or when it has closed below its 200-day average for twenty straight sessions.
Both are facts, and neither is an instruction.

**It will not tell you to sell.** That is not squeamishness — the momentum
rotation rule measured in this repo would have sold NVDA after a 56% fall in
2018 and again after 66% in 2022, which is to say it would have sold exactly the
drawdowns that had to be survived. What the group gives you is the shortlist
worth thinking about.

### Finding something to replace it with

```
/pool US
/pool BIST
/pool US refresh      # rescan now — a few minutes
```

Six hundred names become twenty, filtered on **size** (small enough that large
funds structurally cannot be there), **liquidity** (large enough that you can get
out), and **crowding** (institutions have not already piled in), capped at three
per sector.

The scan runs by itself every Saturday and the result is stored, so `/pool`
answers instantly and tells you when it was screened. You get it unprompted once
a month, on the 1st, with 🆕 against the names that were not in the previous
screen. Weekly delivery was considered and rejected: a fresh list of twenty every
week is a machine for producing trades, and rising trading is the one thing here
measured as reliably harmful to a retail account.

**Candidates are described in exactly the same words as your holdings** — same
four facts, same references, same layout, from the same code. That is deliberate.
Written up more generously than what you already own, the new name always looks
better; described identically, it only looks better when it is.

`ahead` and `behind` are per-metric comparisons against a named reference, not a
verdict, and `3/4 ahead` counts them rather than scoring the company. The
ordering is momentum, which this repo measured and cannot validate — it decides
reading order and nothing else.

Run `/positions` first in a session and the pool will also flag candidates in
sectors you are already heavy in. It flags rather than filters: hiding a good
name to protect you from yourself is a worse trade than telling you the truth.

### Saying no, and having it remembered

```
/pass US ASTH priced for perfection, no margin of safety
```

Two things happen. The name moves to an **Already declined** section at the
bottom of `/pool` instead of arriving each month as a fresh idea — with your own
reason attached, so changing your mind stays possible and stays a decision
rather than a re-run. Buying it later clears the flag; the last call wins.

And it enters the record that `/scorecard` reads.

### Finding out whether your picking is any good

```
/scorecard
```

Everything else here assumes the screen narrows and *you* choose well. This is
the only thing that checks it. Not against the index — against **the names you
saw and declined**:

```
14 bought, 23 passed, median age 502 days. Your picks are +11.4 pp against the
ones you declined, with a standard error of 8.2 pp. That is inside the noise:
it is not yet evidence either way.
```

That is the honest shape of it for a long time. Under ten decisions a side, or
a median age under a year, it refuses to compute anything and says what is
missing instead. Past that it prints the gap next to its standard error and
calls anything inside two of them noise — with per-name volatility near 40% a
year, a dozen picks carry a standard error around 15 points, so a 10-point lead
means nothing.

It was built before there was anything to report, so it cannot be quietly
adjusted into agreeing later. It will report a bad result exactly as readily as
a good one, and that is the point: it is the one measurement that can tell you
this whole approach is not working.

**`/pass` is the unglamorous half and the half that matters.** Purchases alone
can only be compared against the index, which is a different question dominated
by the market. Purchases against passes compare your judgement against the
alternatives you actually had, at the moment you had them.

The terminal version still exists, and prints the sector-mix diagnostics the
Telegram one leaves out:

```
python -m scripts.screen_candidates US
python -m scripts.screen_candidates BIST
```

Filters are not sector-neutral, and the ownership one favours small banks and
REITs for structural reasons.

### Researching one name

```
/brief US ASTH
```

One page: what the company does, growth and margin, where the price sits against
its own history, recent headlines, and **the worst fall it has actually put its
owners through** — the number worth knowing before you buy, not after.

Each number comes with what it measures and where it sits against its own
sector's median. Two things about those comparison lines are worth knowing:

- **They state their sample size.** `sector median 12 (n=114)` and
  `sector median 20 (n=11, thin)` are not equally strong claims, and a median
  built on eleven names moves if two of them change. The page marks the thin
  ones rather than letting them read as firm.
- **They never cross sectors.** A 24% profit margin is ordinary for a bank and
  remarkable for a retailer, because the two are not measuring the same thing.
  These lines rank a company among its peers and nothing else.

**BIST names get no *sector* peers, and cannot.** A hundred names spread over
~34 sectors is about three each — no sample to take a median from, whatever you
re-run. So they fall back to the **whole market** instead: `P/E 4 (market 9,
n=93)`. That is a level this company sits at, not a peer comparison — it mixes a
bank with an airline by construction, and the page says so every time it uses
it. Still far better than nothing, which is what those lines showed before.

US medians are never offered as a BIST substitute. Different economy, different
cost of capital, different normal.

Anything the data source could not supply is listed explicitly. A silent gap
reads as "nothing to report", which is a different claim.

The page ends with five questions no screen can answer. They are the job.

### Writing the thesis

```
/draft US ASTH 45 3 hospital services are consolidating and these are on the buying side, margins improving
```

You get back a ready-to-send line with falsifiers fitted to that specific name:

```
/thesis add US ASTH 45 3 | Consolidating hospital services with ASTH on the
acquiring side and improving margins | drawdown:0.55 | trend:200/4 | growth:0.15
```

Read it. Change anything you disagree with. Send it.

The fields: market, symbol, your entry price, conviction 1–5, then `|` separated —
why you own it, then the falsifiers.

**Conviction sets your position ceiling.** 1 → 3% of the portfolio, 3 → 10%,
5 → 25%. It is not enthusiasm, it is how much of your money this idea is allowed
to be.

### Falsifiers

A falsifier is *"if this happens, I was wrong"*, written while you are calm and
checked later while you are not.

| Spec | Fires when |
|---|---|
| `trend:200/4` | closes below its 200-day average for 4 straight weeks |
| `drawdown:0.5` | falls 50% below its high since you bought |
| `growth:0.20` | revenue growth drops under 20% |
| `margin:0.10` | profit margin drops under 10% |
| `ask: a rival ships at scale` | for you to judge at review time |

**At least one must be machine-checkable.** A thesis whose only exit condition is
"I'll know it when I see it" is refused, because that is not an exit condition.

`/draft` fits the thresholds to the name's own history, so they sit past what it
does in an ordinary bad stretch. A falsifier that fires during normal weakness is
a stop-loss, and the exit-rule research here measured what stop-losses do to a
long hold.

### Auditing what you already own

```
/audit
```

If your positions came from somewhere else — an older system, your own
research — they have no theses attached, so nothing can be monitored and nothing
will alert you.

`/audit` lists every holding with its weight, its ceiling, what the data says,
and whether a thesis exists. It asks one question per position: **would you buy
this today, at this weight, knowing what you know now?**

That matters because "should I sell everything?" cannot be answered, and this
can. A position you cannot write a thesis for has already answered it.

### Living with it

| Command | What it does |
|---|---|
| `/positions` | every holding, grouped by what changed |
| `/pool US` | the research queue, in the same words |
| `/pass US ASTH <why>` | record a name you read and declined |
| `/scorecard` | did your picks beat the ones you passed on? |
| `/thesis` | list your open theses |
| `/thesis ASTH` | one thesis, with every condition's current state |
| `/audit` | every holding, with the question that decides it |
| `/check` | run every condition now |
| `/review` | which theses are due, and their human-only questions |
| `/reviewed ASTH note` | timestamp a review you actually did |
| `/close ASTH reason` | record a sale — and whether anything justified it |
| `/digest` | the weekly summary on demand |
| `/add`, `/remove`, `/holdings`, `/value` | keep positions in sync with Midas |

**What arrives on its own:** a summary on Sundays, the research pool on the 1st
of each month, and nothing else unless a falsifier fires. The daily falsifier
check and the weekly pool scan both run silently. On a multi-year horizon there
is nothing new to say most mornings, and a bot that messages you daily teaches
you to stop reading it.

**When you sell**, `/close` runs your falsifiers first and tells you which of
three situations you are in: something fired (the system working), nothing fired
(so did your thesis break, or did the price just move?), or checks that could not
run — which is *unknown*, not *fine*.

A sale with nothing behind it is recorded as unexplained. Nobody stops you. But
that count, over a year, is the honest measure of whether any of this is worth
running.

---

## What is not automated, and will not be

Whether the business is any good. That is left to you deliberately, not from
laziness: the concentrated sleeve is justified *only* by you knowing something
the market does not. Outsource that and there is no edge left — just a handful of
small companies picked at random, which is a worse bet than an index fund.

The bot does the digging, the watching, the arithmetic and the remembering. The
judgement is the part that was always yours, and it is also the only part with
any chance of being worth something.

---

## Research scripts

Everything under `scripts/research/` is concluded experiments — evidence for the
decisions in SPEC, not part of using the system. They are kept so a future
question ("did anyone check X?") finds the run instead of repeating it.

**Do not use them as a tuning surface.** SPEC §6c: at this portfolio size the
annual edge of an 8-name book has a standard error of ~4 pp over ten years, so
sweeping a parameter until a number improves is fitting noise. This project has
done exactly that twice, which is how the number is known.
