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
python -m scripts.build_sector_stats        # what "normal" looks like per sector
```

The third one is slow (one call per name) and makes the difference between
`/brief` printing "P/E 24" and printing "P/E 24, the median in its sector is 18".
Re-run it a few times a year.

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

### Finding something to look at

```
python -m scripts.screen_candidates US
python -m scripts.screen_candidates BIST
```

Six hundred names become a few dozen, filtered on **size** (small enough that
large funds structurally cannot be there), **liquidity** (large enough that you
can get out), and **crowding** (institutions have not already piled in).

It is a reading list. The ordering is momentum, which this repo has measured and
cannot validate — it decides what to read first and nothing else.

Read the sector-mix line it prints. Filters are not sector-neutral, and the
ownership one favours small banks and REITs for structural reasons.

### Researching one name

```
/brief US ASTH
```

One page: what the company does, growth and margin, where the price sits against
its own history, recent headlines, and **the worst fall it has actually put its
owners through** — the number worth knowing before you buy, not after.

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
| `/thesis` | list your open theses |
| `/thesis ASTH` | one thesis, with every condition's current state |
| `/audit` | every holding, with the question that decides it |
| `/check` | run every condition now |
| `/review` | which theses are due, and their human-only questions |
| `/reviewed ASTH note` | timestamp a review you actually did |
| `/close ASTH reason` | record a sale — and whether anything justified it |
| `/digest` | the weekly summary on demand |
| `/add`, `/remove`, `/holdings`, `/value` | keep positions in sync with Midas |

**What arrives on its own:** a summary on Sundays, and nothing else unless a
falsifier fires. The daily check runs silently. On a multi-year horizon there is
nothing new to say most mornings, and a bot that messages you daily teaches you
to stop reading it.

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
