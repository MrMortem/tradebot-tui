"""Trading strategies for TradeBot.

Each strategy looks at a chronological list of close prices and answers one
question: "given these prices, do I want to be long or flat right now?"

    "buy"  -> the strategy wants a long position
    "sell" -> the strategy wants to be flat (exit any long)
    "hold" -> no opinion / not enough signal

The bot layer is position-aware: it only buys when flat and only sells when
holding, so repeated identical signals never stack orders. All strategies
here are deliberately simple, long-only reference implementations meant as a
starting point - not a source of alpha. Tune the parameters below or add your
own subclass and register it in `get_strategies()`.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

Signal = Tuple[str, str]  # (action, human-readable detail)


# ---------------------------------------------------------------- indicators
def sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    """Wilder-smoothed Relative Strength Index of the latest close."""
    if len(values) < period + 1:
        return None
    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    seed, rest = deltas[:period], deltas[period:]
    avg_gain = sum(d for d in seed if d > 0) / period
    avg_loss = -sum(d for d in seed if d < 0) / period
    for d in rest:
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rate_of_change(values: List[float], lookback: int) -> Optional[float]:
    """Percent change of the latest close vs `lookback` bars ago."""
    if len(values) < lookback + 1 or values[-lookback - 1] == 0:
        return None
    return (values[-1] / values[-lookback - 1] - 1.0) * 100.0


# ------------------------------------------------------------------- classes
class Strategy:
    name: str = "base"
    description: str = ""

    @property
    def warmup(self) -> int:
        """Minimum number of bars needed before signals are meaningful."""
        raise NotImplementedError

    def evaluate(self, closes: List[float]) -> Signal:
        raise NotImplementedError


class SMACrossover(Strategy):
    """Trend following: long while the fast average is above the slow one."""

    name = "SMA crossover"

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        self.fast = fast
        self.slow = slow
        self.description = (
            f"Long while SMA({fast}) is above SMA({slow}), flat when below."
        )

    @property
    def warmup(self) -> int:
        return self.slow + 1

    def evaluate(self, closes: List[float]) -> Signal:
        f = sma(closes, self.fast)
        s = sma(closes, self.slow)
        if f is None or s is None:
            return "hold", "warming up"
        detail = f"SMA{self.fast}={f:.2f} vs SMA{self.slow}={s:.2f}"
        if f > s:
            return "buy", detail + " (bullish)"
        if f < s:
            return "sell", detail + " (bearish)"
        return "hold", detail + " (flat)"


class RSIMeanReversion(Strategy):
    """Contrarian: buy oversold dips, exit into overbought strength."""

    name = "RSI mean reversion"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.description = (
            f"Buy when RSI({period}) < {oversold:g}, "
            f"sell when RSI > {overbought:g}."
        )

    @property
    def warmup(self) -> int:
        # Extra bars let Wilder smoothing settle before we trust the value.
        return self.period * 3

    def evaluate(self, closes: List[float]) -> Signal:
        value = rsi(closes, self.period)
        if value is None:
            return "hold", "warming up"
        detail = f"RSI({self.period})={value:.1f}"
        if value < self.oversold:
            return "buy", detail + f" < {self.oversold:g} (oversold)"
        if value > self.overbought:
            return "sell", detail + f" > {self.overbought:g} (overbought)"
        return "hold", detail + " (neutral)"


class MomentumBreakout(Strategy):
    """Momentum: ride moves once price change exceeds a threshold."""

    name = "Momentum breakout"

    def __init__(self, lookback: int = 30, threshold: float = 1.0) -> None:
        self.lookback = lookback
        self.threshold = threshold
        self.description = (
            f"Buy when {lookback}-bar change > +{threshold:g}%, "
            f"sell when < -{threshold:g}%."
        )

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    def evaluate(self, closes: List[float]) -> Signal:
        roc = rate_of_change(closes, self.lookback)
        if roc is None:
            return "hold", "warming up"
        detail = f"{self.lookback}-bar ROC={roc:+.2f}%"
        if roc > self.threshold:
            return "buy", detail + " (breakout up)"
        if roc < -self.threshold:
            return "sell", detail + " (breakdown)"
        return "hold", detail + " (inside band)"


def get_strategies() -> List[Strategy]:
    """The strategies offered in the TUI, in display order."""
    return [SMACrossover(), RSIMeanReversion(), MomentumBreakout()]
