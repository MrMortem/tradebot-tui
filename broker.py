"""Thin Alpaca REST client used by TradeBot.

Uses plain HTTPS requests (no SDK) so the dependency footprint stays small.
The same class serves both environments; only the base URL changes:

    paper -> https://paper-api.alpaca.markets   (fake money)
    live  -> https://api.alpaca.markets         (real money)

Market data always comes from https://data.alpaca.markets with the IEX feed,
which is available on Alpaca's free plan for both paper and live keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

TIMEOUT = 15  # seconds


class BrokerError(Exception):
    """Raised for any Alpaca API failure, with a human-readable message."""


class AlpacaBroker:
    def __init__(self, key: str, secret: str, paper: bool = True) -> None:
        self.paper = paper
        self.base = PAPER_URL if paper else LIVE_URL
        self._session = requests.Session()
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ core
    def _request(self, method: str, url: str, **kwargs):
        try:
            resp = self._session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise BrokerError(f"network error: {exc}") from exc
        if resp.status_code == 404:
            return None  # callers treat 404 as "not found"
        if not resp.ok:
            try:
                msg = resp.json().get("message", resp.text)
            except ValueError:
                msg = resp.text
            if resp.status_code in (401, 403):
                msg = f"authentication failed ({msg}). Check your API keys."
            raise BrokerError(f"HTTP {resp.status_code}: {msg}")
        if resp.text:
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    # --------------------------------------------------------------- account
    def get_account(self) -> dict:
        data = self._request("GET", f"{self.base}/v2/account")
        if data is None:
            raise BrokerError("account endpoint returned no data")
        return data

    def get_clock(self) -> dict:
        data = self._request("GET", f"{self.base}/v2/clock")
        if data is None:
            raise BrokerError("clock endpoint returned no data")
        return data

    # ------------------------------------------------------------- positions
    def get_positions(self) -> List[dict]:
        data = self._request("GET", f"{self.base}/v2/positions")
        return data or []

    def get_position_qty(self, symbol: str) -> float:
        """Signed share quantity currently held (0.0 if flat)."""
        data = self._request("GET", f"{self.base}/v2/positions/{symbol}")
        if data is None:
            return 0.0
        try:
            return float(data.get("qty", 0))
        except (TypeError, ValueError):
            return 0.0

    def close_position(self, symbol: str) -> Optional[dict]:
        """Market-sell the entire position in `symbol` (fractional-safe)."""
        return self._request("DELETE", f"{self.base}/v2/positions/{symbol}")

    # ---------------------------------------------------------------- orders
    def buy_notional(self, symbol: str, notional: float) -> dict:
        """Market-buy `notional` dollars of `symbol` (fractional shares ok)."""
        body = {
            "symbol": symbol,
            "notional": str(round(notional, 2)),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        data = self._request("POST", f"{self.base}/v2/orders", json=body)
        if data is None:
            raise BrokerError("order endpoint returned no data")
        return data

    # ------------------------------------------------------------------ data
    def get_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 200) -> List[float]:
        """Most-recent close prices for `symbol`, oldest first."""
        start = (datetime.now(timezone.utc) - timedelta(days=365)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        params = {
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            "sort": "desc",          # newest first, so `limit` trims old bars
            "feed": "iex",           # free-plan feed
            "adjustment": "raw",
        }
        data = self._request(
            "GET", f"{DATA_URL}/v2/stocks/{symbol}/bars", params=params
        )
        bars = (data or {}).get("bars") or []
        closes = [float(b["c"]) for b in bars if "c" in b]
        closes.reverse()  # back to chronological order
        return closes
