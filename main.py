"""Tape-Scaled Hybrid DCA for the deterministic CBBTC bars replay.

Each campaign combines a modest starter, range-scaled dip purchases, and three
breakout/pullback additions. A race-wide reserve supports a late deep dip or a
profitable trend. The tape's median direction restricts shallow dip entries;
campaign loss, trailing, age, and endgame exits take priority over additions.

All prices, medians, ratios, and thresholds use integers or exact Fractions.
The SDK owns the protocol and account: only reported fills consume tranches,
and only an account with no position confirms a campaign's full exit. Eligible
tranches share one cash-capped buy, so a reply contains at most one order.
"""

import os
import sys
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars


SYMBOL = "CBBTC"
USDC = 1_000_000
BUY_SLIPPAGE_BPS = 150
# Exits are marketable liquidations; a price gap must not block a risk exit.
SELL_SLIPPAGE_BPS = 10_000
DOWN_RUNGS = (
    (Fraction(1, 2), 100 * USDC),
    (Fraction(3, 4), 100 * USDC),
    (Fraction(1), 100 * USDC),
    (Fraction(13, 10), 50 * USDC),
)
UP_AMOUNTS = (100 * USDC, 100 * USDC, 50 * USDC)


def exact_median(values):
    """Return None for an empty preceding window, never a floating median."""
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return None
    middle = count // 2
    if count % 2:
        return Fraction(ordered[middle])
    return Fraction(ordered[middle - 1] + ordered[middle], 2)


@dataclass(frozen=True)
class Tape:
    close: int
    previous_close: int | None
    session_high: int
    previous_session_high: int
    new_high: bool
    range_ratio: Fraction
    med240: Fraction
    downtrend: bool

    def at_depth(self, fraction):
        return self.close <= self.session_high * (1 - fraction * self.range_ratio)


@dataclass
class Campaign:
    first_fill_index: int
    last_fill_index: int
    peak: int
    down_filled: set[int] = field(default_factory=set)
    up_stage: int = 0
    breakout_high: int | None = None
    breakout_fill_index: int | None = None
    pullback_index: int | None = None
    trail_ever_armed: bool = False
    exit_requested: bool = False


@dataclass(frozen=True)
class Allocation:
    sleeve: str
    notional: int
    stage: int = 0
    breakout_high: int | None = None


@dataclass(frozen=True)
class PendingOrder:
    client_id: str
    side: str
    submitted_seq: int
    allocations: tuple[Allocation, ...] = ()


class TapeScaledHybridDCA:
    def __init__(self):
        self.bar_index = -1
        self.closes = deque(maxlen=240)
        self.session_high = 0
        self.campaign = None
        self.campaigns_started = 0
        self.flat_since_index = None
        self.starter_waiting_for_gate = False
        self.reserve_used = False
        self.any_order_filled = False
        self.safety_qty = 0
        self.pending = None
        self.next_client_id = 1

    def _observe(self, close):
        """Advance only on a CBBTC bar; range is unavailable before 30 closes."""
        previous_close = self.closes[-1] if self.closes else None
        previous_session_high = self.session_high
        new_high = close > previous_session_high
        self.bar_index += 1
        self.session_high = max(self.session_high, close)
        self.closes.append(close)
        if len(self.closes) < 30:
            return None

        closes = list(self.closes)
        high = max(closes)
        range_ratio = min(
            Fraction(3, 50),
            max(Fraction(1, 250), Fraction(high - min(closes), high)),
        )
        med240 = exact_median(closes)
        med60now = exact_median(closes[-60:])
        med60prev = exact_median(closes[-120:-60])
        downtrend = (
            med60prev is not None
            and close < med240 * Fraction(249, 250)
            and med60now < med60prev * Fraction(999, 1000)
        )
        return Tape(
            close, previous_close, self.session_high, previous_session_high, new_high,
            range_ratio, med240, downtrend,
        )

    @staticmethod
    def _average_entry(state, holding):
        """Convert account cost/quantity into the bar's raw price units."""
        symbol = state.symbols[SYMBOL]
        numerator = holding.cost * 10 ** (symbol["qty_scale"] + symbol["price_scale"])
        denominator = holding.qty * 10 ** state.hello["capital"]["quote_scale"]
        return Fraction(numerator, denominator)

    def _client_id(self):
        client_id = f"t{self.next_client_id}"
        self.next_client_id += 1
        return client_id

    def _settle(self, state, batch, close):
        """Resolve intentions using fills, then confirm holdings from the account."""
        holding = state.positions.get(SYMBOL)
        held_qty = holding.qty if holding else 0
        for fill in batch["fills"]:
            if fill["status"] == "filled" and not fill["client_id"].startswith("_"):
                self.any_order_filled = True

        pending = self.pending
        if pending is not None and pending.submitted_seq < state.seq:
            result = next(
                (fill for fill in batch["fills"] if fill["client_id"] == pending.client_id),
                None,
            )
            self.pending = None
            # Every order resolves in the next batch. If a no-bar batch was
            # skipped, its no_bar rejection cannot consume any allocation.
            if result is not None and result["status"] == "filled":
                if pending.side == "buy":
                    for allocation in pending.allocations:
                        if allocation.sleeve == "safety":
                            self.safety_qty = min(int(result["qty"]), held_qty)
                        elif allocation.sleeve == "starter" and held_qty > 0:
                            self.campaign = Campaign(
                                first_fill_index=self.bar_index,
                                last_fill_index=self.bar_index,
                                peak=close,
                            )
                            self.campaigns_started += 1
                            self.starter_waiting_for_gate = False
                        elif allocation.sleeve == "down" and self.campaign is not None:
                            self.campaign.down_filled.add(allocation.stage)
                        elif allocation.sleeve == "up" and self.campaign is not None:
                            self.campaign.up_stage = allocation.stage
                            if allocation.stage == 1:
                                self.campaign.breakout_high = allocation.breakout_high
                                self.campaign.breakout_fill_index = self.bar_index
                        elif allocation.sleeve == "reserve":
                            self.reserve_used = True
                    if self.campaign is not None:
                        self.campaign.last_fill_index = self.bar_index

        self.safety_qty = min(self.safety_qty, held_qty)
        if self.campaign is not None and held_qty == 0:
            self.flat_since_index = self.bar_index
            self.campaign = None
            self.starter_waiting_for_gate = False

    def _buy(self, state, allocations):
        """Fund sleeves in order and submit their combined, all-or-nothing buy."""
        cash_left = state.cash
        minimum = int(state.hello["fees"]["min_notional_quote_units"])
        funded = []
        for allocation in allocations:
            amount = min(allocation.notional, cash_left)
            if amount <= 0 or amount < minimum:
                continue
            funded.append(Allocation(
                allocation.sleeve, amount, allocation.stage, allocation.breakout_high,
            ))
            cash_left -= amount
        if not funded:
            return []

        client_id = self._client_id()
        self.pending = PendingOrder(client_id, "buy", state.seq, tuple(funded))
        return [{
            "side": "buy",
            "client_id": client_id,
            "symbol": SYMBOL,
            "notional_quote_units": sum(part.notional for part in funded),
            "max_slippage_bps": BUY_SLIPPAGE_BPS,
        }]

    def _sell(self, state, quantity):
        holding = state.positions.get(SYMBOL)
        quantity = min(quantity, holding.qty if holding else 0)
        if quantity <= 0:
            return []
        client_id = self._client_id()
        self.pending = PendingOrder(client_id, "sell", state.seq)
        return [{
            "side": "sell",
            "client_id": client_id,
            "symbol": SYMBOL,
            "qty": quantity,
            "max_slippage_bps": SELL_SLIPPAGE_BPS,
        }]

    def _starter_amount(self, tape):
        """Keep the first-entry backstop; later starters use depth or flat time."""
        if self.campaigns_started > 0:
            depth = Fraction(1)
            flat_timeout = (
                self.flat_since_index is not None
                and self.bar_index - self.flat_since_index >= 120
            )
            if tape.at_depth(depth) or flat_timeout:
                return 250 * USDC
            return None

        if self.bar_index >= 420:
            return 125 * USDC
        if self.bar_index >= 180 and tape.downtrend:
            self.starter_waiting_for_gate = True

        if self.starter_waiting_for_gate:
            if not tape.downtrend or tape.at_depth(Fraction(1)):
                return 125 * USDC
            return None

        depth = Fraction(1) if tape.downtrend else Fraction(3, 10)
        if tape.at_depth(depth):
            return 250 * USDC
        if self.bar_index >= 180 and not tape.downtrend:
            return 250 * USDC
        return None

    def _exit_due(self, tape, average_entry):
        campaign = self.campaign
        campaign.peak = max(campaign.peak, tape.close)
        trail_armed = campaign.peak >= average_entry * Fraction(203, 200)
        campaign.trail_ever_armed |= trail_armed
        age = self.bar_index - campaign.first_fill_index
        idle_age = self.bar_index - campaign.last_fill_index
        return (
            campaign.exit_requested
            or tape.close <= average_entry * Fraction(193, 200)
            or (trail_armed and tape.close <= campaign.peak * Fraction(124, 125))
            or (idle_age >= 240 and not campaign.trail_ever_armed)
            or age >= 600
            or (self.bar_index >= 1400 and tape.close >= average_entry * Fraction(201, 200))
            or self.bar_index >= 1436
        )

    def _up_allocation(self, tape, average_entry):
        campaign = self.campaign
        if campaign.up_stage == 1 and campaign.pullback_index is None:
            high = campaign.breakout_high
            # A shallow pullback must occur after the breakout decision. Its
            # confirming uptick must belong to a strictly later CBBTC bar.
            if (
                self.bar_index >= campaign.breakout_fill_index
                and high * (1 - Fraction(1, 5) * tape.range_ratio) <= tape.close < high
            ):
                campaign.pullback_index = self.bar_index

        if tape.close > tape.med240 * (1 + Fraction(3, 2) * tape.range_ratio):
            return None
        cleared_breakout = (
            tape.new_high
            and tape.close >= tape.previous_session_high * (1 + tape.range_ratio / 4)
        )
        if campaign.up_stage == 0:
            if cleared_breakout and tape.close >= average_entry * Fraction(201, 200):
                return Allocation("up", UP_AMOUNTS[0], 1, tape.session_high)
        elif campaign.up_stage == 1:
            if (
                campaign.pullback_index is not None
                and self.bar_index > campaign.pullback_index
                and tape.previous_close is not None
                and tape.close > tape.previous_close
            ):
                return Allocation("up", UP_AMOUNTS[1], 2)
        elif campaign.up_stage == 2 and cleared_breakout:
            return Allocation("up", UP_AMOUNTS[2], 3)
        return None

    def _additions(self, tape, average_entry):
        allocations = []
        for rung, (depth, amount) in enumerate(DOWN_RUNGS, start=1):
            if rung in self.campaign.down_filled:
                continue
            if tape.downtrend and depth < 1:
                continue
            if tape.at_depth(depth):
                allocations.append(Allocation("down", amount, rung))

        up = self._up_allocation(tape, average_entry)
        if up is not None:
            allocations.append(up)

        if not self.reserve_used and self.bar_index >= 960:
            deep_dip = tape.at_depth(Fraction(13, 10))
            late_trend = (
                self.bar_index >= 1200
                and tape.close >= average_entry * Fraction(101, 100)
                and not tape.downtrend
            )
            if deep_dip or late_trend:
                allocations.append(Allocation("reserve", 150 * USDC))
        return allocations

    def on_batch(self, state, batch):
        bar = next((bar for bar in batch["bars"] if bar["symbol"] == SYMBOL), None)
        if bar is None:
            return []

        close = int(bar["c"])
        tape = self._observe(close)
        self._settle(state, batch, close)
        if state.warming_up or self.pending is not None:
            return []

        # Safety inventory is never a campaign and never receives additions.
        # Rejected sells are retried from the account's still-held quantity.
        if self.safety_qty > 0:
            return self._sell(state, self.safety_qty)

        holding = state.positions.get(SYMBOL)
        if holding is not None and holding.qty > 0:
            if self.campaign is None or tape is None:
                return []
            average_entry = self._average_entry(state, holding)
            if self._exit_due(tape, average_entry):
                self.campaign.exit_requested = True
                return self._sell(state, holding.qty)
            return self._buy(state, self._additions(tape, average_entry))

        if self.bar_index >= 1380 and not self.any_order_filled:
            return self._buy(state, [Allocation("safety", 30 * USDC)])
        if tape is None or self.campaigns_started >= 4 or self.bar_index > 1300:
            return []
        amount = self._starter_amount(tape)
        if amount is None:
            return []
        return self._buy(state, [Allocation("starter", amount)])


if __name__ == "__main__":
    makefun_bars.run(TapeScaledHybridDCA().on_batch)
