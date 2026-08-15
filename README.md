# TradeBot — terminal stock-trading bot

A keyboard-driven TUI trading bot built with [Textual](https://textual.textualize.io/)
on top of the [Alpaca](https://alpaca.markets) brokerage API. One switch flips
between **paper trading** (fake money) and **live trading** (real money), with
three built-in strategies to choose from and an in-app credential manager.

```
 PAPER TRADING (fake money) | connected | market open | bot RUNNING
 ┌─ Account ──────────┐  ┌─ Positions ────────────────────────────┐
 │ Equity   $100,412  │  │ AAPL  0.52  191.20  193.85  +1.38 ...  │
 │ Cash      $99,900  │  └────────────────────────────────────────┘
 │ ...                │  ┌─ Log ──────────────────────────────────┐
 └────────────────────┘  │ 09:31:02 PAPER AAPL @ 193.85 — SMA9=.. │
 (●) SMA crossover       │ 09:31:02 PAPER BUY $100 of AAPL — ...  │
 ( ) RSI mean reversion  └────────────────────────────────────────┘
 ( ) Momentum breakout
```

## Setup

Requires Python 3.10+ and a free Alpaca account.

```bash
cd tradebot-tui
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
python app.py
```

Get API keys at alpaca.markets — the **paper** dashboard issues paper keys
instantly with $100k of fake money; **live** keys require a funded brokerage
account and come from the live dashboard. They are separate key pairs.

## Using the app

| Key | Action |
|-----|--------|
| `c` | Open the credential manager for the current mode |
| `m` | Toggle paper ↔ live (live requires typing `LIVE`) |
| `s` | Start / stop the bot |
| `r` | Refresh account + positions now |
| `q` | Quit |

Typical first run: press `c`, paste your paper key ID and secret, save. The
app connects, shows your account, and starts refreshing positions every 15s.
Pick a strategy, set symbols (comma-separated), trade size in dollars, bar
timeframe, and poll interval — then press `s`.

### Credentials

Handled entirely inside the TUI. Keys are stored per mode in
`~/.config/tradebot/credentials.json` with permissions `0600`, and are only
ever sent to Alpaca's API over HTTPS. Delete the file (or re-enter keys with
`c`) at any time.

### Paper vs live

The mode banner is always visible: green = paper, red = live. Switching to
live stops any running bot, demands a typed `LIVE` confirmation, and uses the
separately stored live key pair. Every order the bot logs is tagged `PAPER`
or `LIVE` so there is never ambiguity about which account acted.

## Strategies

All three are **long-only**: `buy` means "be long", `sell` means "go flat".
The bot layer is position-aware, so it buys only when flat and sells only
when holding — repeated signals never stack orders. Buys are placed as
notional market orders (e.g. $100 worth, fractional shares); sells close the
whole position.

1. **SMA crossover** — long while the 9-bar SMA is above the 21-bar SMA,
   flat when below. Classic trend following.
2. **RSI mean reversion** — buy when 14-bar Wilder RSI drops under 30,
   exit when it rises over 70. Contrarian dip buying.
3. **Momentum breakout** — buy when the 30-bar rate of change exceeds +1%,
   exit under −1%. Rides sustained moves.

Parameters live at the top of each class in `strategies.py`. To add your own
strategy, subclass `Strategy`, implement `warmup` and
`evaluate(closes) -> (action, detail)`, and add it to `get_strategies()` —
it appears in the TUI automatically.

## How the loop works

Every poll interval the bot: checks the market clock (sleeps while closed) →
fetches recent close bars per symbol (IEX feed, free plan) → asks the active
strategy for a signal → compares against the current position → places at
most one order per symbol per cycle → logs everything with the indicator
values that drove the decision.

## Files

- `app.py` — Textual UI, modals, bot loop
- `broker.py` — minimal Alpaca REST client (paper/live/data endpoints)
- `strategies.py` — indicators + the three strategies
- `credentials.py` — per-mode key storage
- `tests/test_strategies.py` — sanity checks (`python tests/test_strategies.py`)

## Disclaimer

Educational software provided as-is, not financial advice. The bundled
strategies are simple reference implementations and are not expected to be
profitable. Live trading can lose real money quickly — run in paper mode
until you have watched the bot behave through full market days, and only
ever trade money you can afford to lose.
