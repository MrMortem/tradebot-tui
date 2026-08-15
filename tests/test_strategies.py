"""Quick sanity checks for strategy math. Run: python tests/test_strategies.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies import (  # noqa: E402
    MomentumBreakout,
    RSIMeanReversion,
    SMACrossover,
    get_strategies,
    rate_of_change,
    rsi,
    sma,
)


def test_indicators():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2], 5) is None

    up = [100 + i for i in range(60)]        # straight up -> RSI ~100
    down = [200 - i for i in range(60)]      # straight down -> RSI ~0
    assert rsi(up, 14) > 95
    assert rsi(down, 14) < 5
    flatish = [100, 101] * 40                # choppy -> mid RSI
    assert 25 < rsi(flatish, 14) < 75

    assert abs(rate_of_change([100] * 30 + [110], 30) - 10.0) < 1e-9


def test_sma_crossover():
    s = SMACrossover(fast=3, slow=5)
    rising = [1, 2, 3, 4, 5, 6, 7, 8]
    falling = rising[::-1]
    assert s.evaluate(rising)[0] == "buy"
    assert s.evaluate(falling)[0] == "sell"
    assert s.evaluate([1, 2])[0] == "hold"   # not enough data


def test_rsi_strategy():
    s = RSIMeanReversion(period=14)
    assert s.evaluate([200 - i * 2 for i in range(60)])[0] == "buy"    # crash
    assert s.evaluate([100 + i * 2 for i in range(60)])[0] == "sell"   # spike
    action, _ = s.evaluate([100, 101] * 40)
    assert action == "hold"


def test_momentum():
    s = MomentumBreakout(lookback=10, threshold=1.0)
    assert s.evaluate([100] * 20 + [105])[0] == "buy"
    assert s.evaluate([100] * 20 + [95])[0] == "sell"
    assert s.evaluate([100] * 20 + [100.5])[0] == "hold"


def test_registry():
    strats = get_strategies()
    assert len(strats) >= 3
    for s in strats:
        assert s.name and s.description and s.warmup > 0


if __name__ == "__main__":
    for fn in [v for k, v in list(globals().items()) if k.startswith("test_")]:
        fn()
        print(f"ok  {fn.__name__}")
    print("All strategy tests passed.")
