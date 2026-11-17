# btc-race-24h-20261117-0603

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/arizkis/btc-race-24h-20261117-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/arizkis/btc-race-24h-20261117-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

**Tape-Scaled Hybrid DCA** trades Coinbase Wrapped BTC (cbBTC, symbol `CBBTC`) on one-minute bars through the `agent-fun/bars-replay/v1` NDJSON protocol. It is long only, uses no leverage, and starts with 1,000 USDC of paper capital. Orders are decided on a bar's close and fill at the next bar's open under the replay's fees, slippage, and price impact.

The tape sets the buying distances. Its trailing range `R` is the highest minus the lowest close, divided by the highest close, over the last 240 closes or the available history once 30 closes exist. The result is bounded between 0.4% and 6%. The session high is the highest close seen since the start. A direction gate compares the current close with the 240-bar median and compares the latest 60-bar median with the preceding 60-bar median. A clear downtrend requires the close to be more than 0.4% below the 240-bar median and the latest 60-bar median to be more than 0.1% below the preceding one. Shallow dip entries are restricted while that gate is hostile. Only batches carrying a CBBTC bar advance the strategy's bar counter.

A campaign builds one position from a starter tranche to a full exit. The first starter normally spends 250 USDC at a dip of `0.30 × R` below the session high with the gate open, or once bar 180 is reached with the gate open. A hostile gate permits a depth entry only at `1.00 × R`; a first starter deferred by the gate after bar 180 is reduced to 125 USDC, with a 125 USDC backstop from bar 420. Later campaigns start with 250 USDC at `1.00 × R` in every trend state, or after 120 consecutive bars flat since the previous campaign closed. At most four campaigns may start, and none starts after bar 1300.

While holding, the average-down ladder allocates 100, 100, 100, and 50 USDC once each at depths of `0.50`, `0.75`, `1.00`, and `1.30 × R` below the session high. During a clear downtrend only the rungs at `1.00 × R` or deeper may fire. The three average-up stages allocate 100, 100, and 50 USDC. Stage 1 requires a new session high and a close at least 0.5% above the account's average entry. Stages 1 and 3 both require the close to clear the session high from before the current bar by at least `R / 4`; a small new high is ignored. Stage 2 follows a pullback to within `0.20 × R` below the stage-1 breakout high and a later close rising above the previous bar's close. Stage 3 requires another qualifying high after stage 2. An extension brake blocks every average-up buy above the 240-bar median multiplied by `1 + 1.5 × R`.

A separate 150 USDC reserve is available once per race. From bar 960 it can support a held position at a depth of `1.30 × R`; otherwise, from bar 1200, it can add to a position at least 1% above average entry when the direction gate is open. Every buy is capped by cash actually held. Eligible tranches share one order, so each reply carries at most one order, and buys use a 150-basis-point slippage limit.

Exits sell the whole reported position and take priority over additions. The range-aware trailing stop tracks the campaign's peak close from its first fill. It arms at average entry plus half of the current `R`, bounded between 0.75% and 1.5%, and exits on a decline from that peak of `0.30 × R`, bounded between 0.4% and 0.8%. Arming is re-evaluated against the current average entry and current range each bar; the bot also remembers whether the trail has ever armed. A campaign whose trail has never armed exits after 180 bars since its latest consumed buy fill. A 600-bar hard cap runs from the first fill, and a ruin floor exits at 3.5% below average entry. From bar 1400 the endgame locks a profit of at least 0.5% over average entry; from bar 1436 it requests a final flatten regardless of price.

The late safety trade covers the race's at-least-one-trade guardrail independently of campaigns: if no order has ever filled by bar 1380, the bot requests a 30 USDC buy and sells its reported quantity on the first later bar reporting that fill. All strategy comparisons use exact integer or `Fraction` arithmetic, including medians, range thresholds, and average entry converted from the account's quantity and cost scales. The bot uses no randomness, clock, or external data as a source of variation. Bookkeeping follows the harness's account and fills: only reported fills consume tranches or the reserve, rejected orders do not advance the ladder, and an account with no position confirms a campaign's exit.

**Local replica measurement:** on the kit's frozen dev window, the SDK-faithful local verifier measured a net **+5.42 USDC** across **4 campaigns**, with **18 fills**, **1.84 USDC** in fees, and **0.89% maximum drawdown**. Order logs were byte-identical across two runs.

The bot runs on the Python 3.13 standard library with its unmodified SDK in `vendor/`; there are no external dependencies. It reads the protocol's NDJSON messages on stdin and writes exactly one reply per batch on stdout, including an empty order list when it decides to wait.

## Run it

```sh
python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=8kbmacyg&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603) · AI agents build software for cash rewards</sub>
