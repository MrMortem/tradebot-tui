"""TradeBot - a terminal stock-trading bot built on Textual + Alpaca.

Features
--------
* Paper / live toggle (press "m"). Live mode requires typing LIVE to confirm
  and uses its own separately stored API key pair.
* Three switchable strategies (SMA crossover, RSI mean reversion, momentum
  breakout) - see strategies.py.
* In-app credential manager (press "c"). Keys are stored per mode in
  ~/.config/tradebot/credentials.json, chmod 600.
* Live dashboard: account snapshot, open positions with P/L, activity log.

This is educational software, not financial advice. Markets involve risk of
loss; run in paper mode until you trust the behavior end to end.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional, Tuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
)

from broker import AlpacaBroker, BrokerError
from credentials import CRED_FILE, load_credentials, save_credentials
from strategies import Strategy, get_strategies

PAPER_BG = "#1b5e20"   # deep green banner for fake money
LIVE_BG = "#7f1010"    # deep red banner for real money
REFRESH_SECS = 15      # account/positions refresh cadence
MIN_POLL_SECS = 5

TIMEFRAMES = [
    ("1 minute bars", "1Min"),
    ("5 minute bars", "5Min"),
    ("15 minute bars", "15Min"),
    ("1 day bars", "1Day"),
]


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


# --------------------------------------------------------------------- modals
class CredentialsScreen(ModalScreen[Optional[Tuple[str, str]]]):
    """Enter/update the Alpaca key pair for the mode being edited."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, mode: str, existing_key: str = "") -> None:
        super().__init__()
        self.mode = mode
        self.existing_key = existing_key

    def compose(self) -> ComposeResult:
        title = f"{self.mode.upper()} API keys"
        hint = (
            "Paper keys come from the paper dashboard; live keys from the "
            "live dashboard at alpaca.markets. Stored locally, chmod 600."
        )
        with Vertical(id="dialog"):
            yield Label(title, id="dialog-title")
            yield Label(hint, classes="hint")
            yield Label("API key ID")
            yield Input(value=self.existing_key, placeholder="PK...", id="key")
            yield Label("API secret key")
            yield Input(password=True, placeholder="secret", id="secret")
            with Horizontal(classes="buttons"):
                yield Button("Save keys", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            key = self.query_one("#key", Input).value.strip()
            secret = self.query_one("#secret", Input).value.strip()
            if not key or not secret:
                self.app.notify("Both fields are required.", severity="warning")
                return
            self.dismiss((key, secret))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmLiveScreen(ModalScreen[bool]):
    """Hard gate before switching to real money."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Switch to LIVE trading?", id="dialog-title")
            yield Label(
                "Orders will be placed with REAL MONEY on your live "
                "Alpaca account. Losses are real and can be fast.",
                id="warn",
            )
            yield Label('Type LIVE to confirm:')
            yield Input(placeholder="LIVE", id="confirm")
            with Horizontal(classes="buttons"):
                yield Button("Enable live trading", variant="error", id="go")
                yield Button("Stay in paper", variant="primary", id="stay")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go":
            if self.query_one("#confirm", Input).value.strip() == "LIVE":
                self.dismiss(True)
            else:
                self.app.notify(
                    "Type LIVE (all caps) to confirm.", severity="warning"
                )
        else:
            self.dismiss(False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip() == "LIVE":
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ------------------------------------------------------------------ main app
class TradeBotApp(App):
    TITLE = "TradeBot"
    SUB_TITLE = "Alpaca terminal trading bot"

    CSS = """
    #banner {
        height: 1;
        content-align: center middle;
        text-style: bold;
        color: white;
        background: #1b5e20;
    }
    #body { height: 1fr; }
    #sidebar {
        width: 38;
        padding: 0 1;
        border-right: solid $panel;
    }
    #sidebar > Label { margin-top: 1; color: $text-muted; }
    #sidebar Button { width: 100%; margin-top: 1; }
    #account {
        border: round $primary;
        border-title-color: $text-muted;
        padding: 0 1;
        height: auto;
        min-height: 6;
        margin-top: 1;
    }
    #strategy { width: 100%; }
    #positions { height: 40%; border: round $primary; }
    #log { height: 1fr; border: round $primary; padding: 0 1; }

    CredentialsScreen, ConfirmLiveScreen { align: center middle; }
    #dialog {
        width: 62;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog Label { margin-top: 1; }
    #dialog-title { text-style: bold; margin-top: 0; }
    #dialog .hint { color: $text-muted; }
    #warn { color: $error; text-style: bold; }
    .buttons { height: auto; margin-top: 1; }
    .buttons Button { margin-right: 2; }
    """

    BINDINGS = [
        Binding("s", "toggle_bot", "Start/Stop bot"),
        Binding("m", "toggle_mode", "Paper/Live"),
        Binding("c", "credentials", "Credentials"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.mode: str = "paper"                 # "paper" | "live"
        self.broker: Optional[AlpacaBroker] = None
        self.strategies: List[Strategy] = get_strategies()
        self.strategy: Strategy = self.strategies[0]
        self.bot_worker = None
        self.market_open: Optional[bool] = None
        self._closed_logged = False
        self._refreshing = False

    # ------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="banner")
        with Horizontal(id="body"):
            with VerticalScroll(id="sidebar"):
                yield Static("Not connected.", id="account")
                yield Label("Strategy")
                with RadioSet(id="strategy"):
                    for i, strat in enumerate(self.strategies):
                        yield RadioButton(strat.name, value=(i == 0))
                yield Label("Symbols (comma-separated)")
                yield Input(value="AAPL", id="symbols")
                yield Label("Trade size per buy (USD)")
                yield Input(value="100", id="notional", type="number")
                yield Label("Bar timeframe")
                yield Select(
                    TIMEFRAMES, value="1Min", allow_blank=False, id="timeframe"
                )
                yield Label("Check signals every (seconds)")
                yield Input(value="60", id="poll", type="integer")
                yield Button("Start bot", variant="success", id="startstop")
                yield Button("Switch to live", variant="warning", id="modebtn")
                yield Button("Credentials", id="credsbtn")
            with Vertical(id="main"):
                yield DataTable(id="positions")
                yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#positions", DataTable)
        table.add_columns("Symbol", "Qty", "Avg $", "Now $", "P/L $", "P/L %")
        table.cursor_type = "row"
        table.zebra_stripes = True
        acct = self.query_one("#account", Static)
        acct.border_title = "Account"

        self._log("[b]TradeBot[/b] ready.")
        self._log(
            "[dim]Educational software - not financial advice. Trading risks "
            "real losses; stay in paper mode until fully tested.[/dim]"
        )
        self._update_banner()

        if load_credentials(self.mode):
            self.run_worker(self._connect(), exclusive=True, group="connect")
        else:
            self._log(
                f"No {self.mode} credentials stored yet - press [b]c[/b] to "
                "add your Alpaca API keys."
            )
        self.set_interval(REFRESH_SECS, self._periodic_refresh)

    # ------------------------------------------------------------ helpers
    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(f"[dim]{now_str()}[/dim] {message}")

    @property
    def bot_running(self) -> bool:
        return self.bot_worker is not None and self.bot_worker.is_running

    def _update_banner(self) -> None:
        banner = self.query_one("#banner", Static)
        if self.mode == "paper":
            mode_txt = "PAPER TRADING (fake money)"
            banner.styles.background = PAPER_BG
        else:
            mode_txt = "LIVE TRADING - REAL MONEY"
            banner.styles.background = LIVE_BG
        conn = "connected" if self.broker else "not connected"
        if self.market_open is True:
            market = "market open"
        elif self.market_open is False:
            market = "market closed"
        else:
            market = "market ?"
        bot = "bot RUNNING" if self.bot_running else "bot stopped"
        banner.update(f" {mode_txt}   |   {conn}   |   {market}   |   {bot} ")

        modebtn = self.query_one("#modebtn", Button)
        modebtn.label = (
            "Switch to live" if self.mode == "paper" else "Switch to paper"
        )
        startstop = self.query_one("#startstop", Button)
        startstop.label = "Stop bot" if self.bot_running else "Start bot"
        startstop.variant = "error" if self.bot_running else "success"

    # ----------------------------------------------------------- settings
    def _read_symbols(self) -> List[str]:
        raw = self.query_one("#symbols", Input).value
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    def _read_float(self, widget_id: str, fallback: float) -> float:
        try:
            return float(self.query_one(widget_id, Input).value)
        except (ValueError, TypeError):
            return fallback

    # ------------------------------------------------------------- events
    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "strategy":
            return
        self.strategy = self.strategies[event.index]
        self._log(
            f"Strategy -> [b]{self.strategy.name}[/b]: "
            f"{self.strategy.description}"
        )
        if self.bot_running:
            self._log("[yellow]Restart the bot to apply the change.[/yellow]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "startstop":
            self.action_toggle_bot()
        elif event.button.id == "modebtn":
            self.action_toggle_mode()
        elif event.button.id == "credsbtn":
            self.action_credentials()

    # ------------------------------------------------------------ actions
    def action_credentials(self) -> None:
        existing = load_credentials(self.mode)
        screen = CredentialsScreen(self.mode, existing[0] if existing else "")
        self.push_screen(screen, self._credentials_saved)

    def _credentials_saved(self, result: Optional[Tuple[str, str]]) -> None:
        if not result:
            return
        path = save_credentials(self.mode, *result)
        self._log(f"Saved {self.mode} credentials to [dim]{path}[/dim]")
        self.run_worker(self._connect(), exclusive=True, group="connect")

    def action_toggle_mode(self) -> None:
        if self.bot_running:
            self._stop_bot("mode change")
        if self.mode == "paper":
            self.push_screen(ConfirmLiveScreen(), self._live_confirmed)
        else:
            self._switch_mode("paper")

    def _live_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self._switch_mode("live")
        else:
            self._log("Staying in paper mode.")

    def _switch_mode(self, mode: str) -> None:
        self.mode = mode
        self.broker = None
        self.market_open = None
        self._update_banner()
        label = "LIVE - real money" if mode == "live" else "paper - fake money"
        self._log(f"Mode -> [b]{label}[/b]")
        if load_credentials(mode):
            self.run_worker(self._connect(), exclusive=True, group="connect")
        else:
            self._log(f"No {mode} credentials stored - enter them to connect.")
            self.action_credentials()

    def action_refresh(self) -> None:
        self.run_worker(self._refresh_data(), exclusive=True, group="refresh")

    def action_toggle_bot(self) -> None:
        if self.bot_running:
            self._stop_bot("user request")
            return
        if not self.broker:
            self.notify("Connect first - press c for credentials.", severity="warning")
            return
        symbols = self._read_symbols()
        if not symbols:
            self.notify("Enter at least one symbol.", severity="warning")
            return
        notional = self._read_float("#notional", 0)
        if notional <= 0:
            self.notify("Trade size must be a positive dollar amount.", severity="warning")
            return
        poll = max(MIN_POLL_SECS, int(self._read_float("#poll", 60)))
        timeframe = self.query_one("#timeframe", Select).value
        self.bot_worker = self.run_worker(
            self._bot_loop(symbols, notional, str(timeframe), poll),
            exclusive=True,
            group="bot",
        )
        self._update_banner()

    def _stop_bot(self, reason: str) -> None:
        if self.bot_worker is not None:
            self.bot_worker.cancel()
            self.bot_worker = None
        self._log(f"[b]Bot stopped[/b] ({reason}).")
        self._update_banner()

    # ---------------------------------------------------------- connection
    async def _connect(self) -> None:
        creds = load_credentials(self.mode)
        if not creds:
            return
        broker = AlpacaBroker(*creds, paper=(self.mode == "paper"))
        try:
            account = await asyncio.to_thread(broker.get_account)
        except BrokerError as exc:
            self.broker = None
            self._log(f"[red]Connection failed:[/red] {exc}")
            self._update_banner()
            return
        self.broker = broker
        equity = float(account.get("equity", 0) or 0)
        self._log(
            f"[green]Connected[/green] to {self.mode} account "
            f"{account.get('account_number', '?')} - equity ${equity:,.2f}"
        )
        await self._refresh_data()

    # ------------------------------------------------------------- refresh
    async def _periodic_refresh(self) -> None:
        if self.broker and not self._refreshing:
            await self._refresh_data()

    async def _refresh_data(self) -> None:
        if not self.broker:
            return
        self._refreshing = True
        try:
            account, positions, clock = await asyncio.gather(
                asyncio.to_thread(self.broker.get_account),
                asyncio.to_thread(self.broker.get_positions),
                asyncio.to_thread(self.broker.get_clock),
            )
        except BrokerError as exc:
            self._log(f"[red]Refresh failed:[/red] {exc}")
            return
        finally:
            self._refreshing = False

        self.market_open = bool(clock.get("is_open"))
        self._update_account(account)
        self._update_positions(positions)
        self._update_banner()

    def _update_account(self, account: dict) -> None:
        def money(field: str) -> str:
            try:
                return f"${float(account.get(field, 0) or 0):,.2f}"
            except (TypeError, ValueError):
                return "-"

        text = (
            f"[b]Equity[/b]        {money('equity')}\n"
            f"[b]Cash[/b]          {money('cash')}\n"
            f"[b]Buying power[/b]  {money('buying_power')}\n"
            f"[b]Status[/b]        {account.get('status', '?')}"
        )
        self.query_one("#account", Static).update(text)

    def _update_positions(self, positions: List[dict]) -> None:
        table = self.query_one("#positions", DataTable)
        table.clear()
        for p in positions:
            try:
                pl = float(p.get("unrealized_pl", 0) or 0)
                plpc = float(p.get("unrealized_plpc", 0) or 0) * 100
                style = "green" if pl >= 0 else "red"
                table.add_row(
                    p.get("symbol", "?"),
                    p.get("qty", "?"),
                    f"{float(p.get('avg_entry_price', 0) or 0):,.2f}",
                    f"{float(p.get('current_price', 0) or 0):,.2f}",
                    Text(f"{pl:+,.2f}", style=style),
                    Text(f"{plpc:+.2f}%", style=style),
                )
            except (TypeError, ValueError):
                continue

    # ------------------------------------------------------------ bot loop
    async def _bot_loop(
        self, symbols: List[str], notional: float, timeframe: str, poll: int
    ) -> None:
        strategy = self.strategy
        tag = "[red b]LIVE[/red b]" if self.mode == "live" else "[green]PAPER[/green]"
        self._log(
            f"[b]Bot started[/b] {tag} - {strategy.name} on "
            f"{', '.join(symbols)}, {timeframe} bars, ${notional:g} per buy, "
            f"checking every {poll}s."
        )
        self._closed_logged = False
        try:
            while True:
                try:
                    await self._bot_cycle(symbols, notional, timeframe, strategy, tag)
                except BrokerError as exc:
                    self._log(f"[red]Cycle error:[/red] {exc}")
                except Exception as exc:  # keep the loop alive on surprises
                    self._log(f"[red]Unexpected error:[/red] {exc!r}")
                await asyncio.sleep(poll)
        except asyncio.CancelledError:
            raise

    async def _bot_cycle(
        self,
        symbols: List[str],
        notional: float,
        timeframe: str,
        strategy: Strategy,
        tag: str,
    ) -> None:
        if not self.broker:
            return
        clock = await asyncio.to_thread(self.broker.get_clock)
        self.market_open = bool(clock.get("is_open"))
        self._update_banner()
        if not self.market_open:
            if not self._closed_logged:
                self._log(
                    "Market closed - waiting. "
                    f"Next open: {clock.get('next_open', '?')}"
                )
                self._closed_logged = True
            return
        self._closed_logged = False

        limit = max(strategy.warmup + 10, 120)
        for symbol in symbols:
            closes = await asyncio.to_thread(
                self.broker.get_bars, symbol, timeframe, limit
            )
            if len(closes) < strategy.warmup:
                self._log(
                    f"{symbol}: only {len(closes)} bars "
                    f"(need {strategy.warmup}) - skipping."
                )
                continue
            action, detail = strategy.evaluate(closes)
            qty = await asyncio.to_thread(self.broker.get_position_qty, symbol)
            price = closes[-1]
            self._log(
                f"{tag} {symbol} @ {price:,.2f} - {detail} -> "
                f"[b]{action.upper()}[/b] (holding {qty:g})"
            )
            if action == "buy" and qty <= 0:
                order = await asyncio.to_thread(
                    self.broker.buy_notional, symbol, notional
                )
                self._log(
                    f"{tag} [green b]BUY[/green b] ${notional:g} of {symbol} "
                    f"- order {order.get('id', '?')[:8]} "
                    f"({order.get('status', '?')})"
                )
            elif action == "sell" and qty > 0:
                await asyncio.to_thread(self.broker.close_position, symbol)
                self._log(
                    f"{tag} [red b]SELL[/red b] closed {qty:g} {symbol}"
                )


if __name__ == "__main__":
    TradeBotApp().run()
