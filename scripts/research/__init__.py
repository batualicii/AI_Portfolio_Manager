"""Concluded experiments. Evidence, not product.

Everything here answered a question and the answers are written up in SPEC §0
and §6c. They are kept because the reasoning behind a decision is worth more
than the decision, and because a future session that wonders "did anyone check
X?" deserves to find the run rather than repeat it.

**Do not treat these as a tuning surface.** SPEC §6c is explicit: at this
portfolio size no backtest here can establish an edge — the annual edge of an
8-name book has a standard error of ~4 pp over ten years. Searching this data
for a better parameter is fitting noise, and this project has already done it
twice. If you find yourself sweeping a threshold until a number improves, that
is the failure mode, not the method.

`search_null.py` is the exception, and the only file here you should still run.
It performs exactly that sweep — every indicator, every weight, every basket
size — and then runs the identical sweep on hundreds of worlds with the
predictability surgically removed, so the winner is judged against the best
obtainable from nothing rather than against zero. Its synthetic test pins the
number that matters: **a search of a few hundred combinations finds roughly
+9 pp/yr of "excess return" in data containing no signal at all** — the same
order as the +6.6 pp/yr this repo once believed it had found. Run it when the
urge to tune returns, which it will.
"""
