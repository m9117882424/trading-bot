from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class MarketDataService:
    BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"
    BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
    TWELVE_DATA_TIME_SERIES_URL = "https://api.twelvedata.com/time_series"

    async def _fetch_json(self, url: str, params: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _get_bybit_klines(
        self,
        symbol: str,
        interval: str,
        category: str = "linear",
        limit: int = 20,
    ) -> list[list[Any]]:
        data = await self._fetch_json(
            self.BYBIT_KLINES_URL,
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit kline error for {symbol}: {data}")

        rows = data.get("result", {}).get("list", [])
        if not rows:
            raise RuntimeError(f"No Bybit kline data for {symbol}")

        # Bybit returns reverse-sorted candles by startTime; convert to oldest -> newest
        rows = list(reversed(rows))
        return rows

    async def _bybit_symbol_exists(self, symbol: str, category: str = "linear") -> bool:
        data = await self._fetch_json(
            self.BYBIT_INSTRUMENTS_URL,
            {
                "category": category,
                "symbol": symbol,
            },
        )
        if data.get("retCode") != 0:
            return False

        rows = data.get("result", {}).get("list", [])
        return any(item.get("symbol") == symbol for item in rows)

    def _normalize_bybit_klines(
        self,
        asset: str,
        klines_4h: list[list[Any]],
        klines_1d: list[list[Any]],
        source: str,
    ) -> dict[str, Any]:
        if not klines_4h or not klines_1d:
            raise RuntimeError(f"No Bybit kline data for {asset}")

        # Bybit candle format:
        # [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        last_4h = klines_4h[-1]
        prev_4h = klines_4h[-2] if len(klines_4h) > 1 else klines_4h[-1]

        last_1d = klines_1d[-1]
        prev_1d = klines_1d[-2] if len(klines_1d) > 1 else klines_1d[-1]

        close_4h = float(last_4h[4])
        open_4h = float(last_4h[1])
        high_4h = float(last_4h[2])
        low_4h = float(last_4h[3])

        close_1d = float(last_1d[4])
        open_1d = float(last_1d[1])
        high_1d = float(last_1d[2])
        low_1d = float(last_1d[3])

        prev_close_1d = float(prev_1d[4])
        change_24h = ((close_1d - prev_close_1d) / prev_close_1d * 100) if prev_close_1d else 0.0

        return {
            "asset": asset,
            "price": close_4h,
            "change_24h": round(change_24h, 4),
            "high_24h": high_1d,
            "low_24h": low_1d,
            "trend_4h": "up" if close_4h >= open_4h else "down",
            "trend_1d": "up" if close_1d >= open_1d else "down",
            "enabled": True,
            "skip_reason": None,
            "context_4h": {
                "open": open_4h,
                "high": high_4h,
                "low": low_4h,
                "close": close_4h,
                "prev_close": float(prev_4h[4]),
                "recent_closes": [float(x[4]) for x in klines_4h[-5:]],
                "source": source,
            },
            "context_1d": {
                "open": open_1d,
                "high": high_1d,
                "low": low_1d,
                "close": close_1d,
                "prev_close": prev_close_1d,
                "recent_closes": [float(x[4]) for x in klines_1d[-5:]],
                "source": source,
            },
        }

    async def _get_twelve_time_series(self, symbol: str, interval: str, outputsize: int = 20) -> dict[str, Any]:
        if not settings.twelve_data_api_key:
            raise RuntimeError("TWELVE_DATA_API_KEY is not set")

        data = await self._fetch_json(
            self.TWELVE_DATA_TIME_SERIES_URL,
            {
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": settings.twelve_data_api_key,
            },
        )

        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"Twelve Data error for {symbol}: {data}")

        return data

    def _normalize_twelve_series(
        self,
        asset: str,
        series_4h: dict[str, Any],
        series_1d: dict[str, Any],
    ) -> dict[str, Any]:
        values_4h = series_4h.get("values", [])
        values_1d = series_1d.get("values", [])

        if not values_4h or not values_1d:
            raise RuntimeError(f"No Twelve Data time series for {asset}")

        # Twelve Data returns newest first
        last_4h = values_4h[0]
        prev_4h = values_4h[1] if len(values_4h) > 1 else values_4h[0]

        last_1d = values_1d[0]
        prev_1d = values_1d[1] if len(values_1d) > 1 else values_1d[0]

        close_4h = float(last_4h["close"])
        open_4h = float(last_4h["open"])
        high_4h = float(last_4h["high"])
        low_4h = float(last_4h["low"])

        close_1d = float(last_1d["close"])
        open_1d = float(last_1d["open"])
        high_1d = float(last_1d["high"])
        low_1d = float(last_1d["low"])

        prev_close_1d = float(prev_1d["close"])
        change_24h = ((close_1d - prev_close_1d) / prev_close_1d * 100) if prev_close_1d else 0.0

        return {
            "asset": asset,
            "price": close_4h,
            "change_24h": round(change_24h, 4),
            "high_24h": high_1d,
            "low_24h": low_1d,
            "trend_4h": "up" if close_4h >= open_4h else "down",
            "trend_1d": "up" if close_1d >= open_1d else "down",
            "enabled": True,
            "skip_reason": None,
            "context_4h": {
                "open": open_4h,
                "high": high_4h,
                "low": low_4h,
                "close": close_4h,
                "prev_close": float(prev_4h["close"]),
                "recent_closes": [float(x["close"]) for x in values_4h[:5]],
                "source": "twelve_data",
            },
            "context_1d": {
                "open": open_1d,
                "high": high_1d,
                "low": low_1d,
                "close": close_1d,
                "prev_close": prev_close_1d,
                "recent_closes": [float(x["close"]) for x in values_1d[:5]],
                "source": "twelve_data",
            },
        }

    def _skipped_asset(self, asset: str, reason: str) -> dict[str, Any]:
        return {
            "asset": asset,
            "price": 0.0,
            "change_24h": 0.0,
            "high_24h": 0.0,
            "low_24h": 0.0,
            "trend_4h": "unknown",
            "trend_1d": "unknown",
            "enabled": False,
            "skip_reason": reason,
            "context_4h": {"error": reason},
            "context_1d": {"error": reason},
        }

    async def get_data(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        # BTC via Bybit linear futures
        try:
            btc_4h = await self._get_bybit_klines("BTCUSDT", "240", category="linear", limit=20)
            btc_1d = await self._get_bybit_klines("BTCUSDT", "D", category="linear", limit=20)
            result["BTC"] = self._normalize_bybit_klines(
                "BTC",
                btc_4h,
                btc_1d,
                source="bybit_linear",
            )
        except Exception as e:
            result["BTC"] = self._skipped_asset("BTC", f"Bybit linear error: {e}")

        # XAGUSDT via Bybit linear futures
        try:
            if await self._bybit_symbol_exists("XAGUSDT", category="linear"):
                xag_4h = await self._get_bybit_klines("XAGUSDT", "240", category="linear", limit=20)
                xag_1d = await self._get_bybit_klines("XAGUSDT", "D", category="linear", limit=20)
                result["XAGUSDT"] = self._normalize_bybit_klines(
                    "XAGUSDT",
                    xag_4h,
                    xag_1d,
                    source="bybit_linear",
                )
            else:
                result["XAGUSDT"] = self._skipped_asset(
                    "XAGUSDT",
                    "Bybit linear symbol not available",
                )
        except Exception as e:
            result["XAGUSDT"] = self._skipped_asset("XAGUSDT", f"Bybit linear error: {e}")

        # XAUT via Bybit linear futures if available, else skip
        try:
            if await self._bybit_symbol_exists("XAUTUSDT", category="linear"):
                xaut_4h = await self._get_bybit_klines("XAUTUSDT", "240", category="linear", limit=20)
                xaut_1d = await self._get_bybit_klines("XAUTUSDT", "D", category="linear", limit=20)
                result["XAUT"] = self._normalize_bybit_klines(
                    "XAUT",
                    xaut_4h,
                    xaut_1d,
                    source="bybit_linear",
                )
            else:
                result["XAUT"] = self._skipped_asset(
                    "XAUT",
                    "Bybit linear symbol not available",
                )
        except Exception as e:
            result["XAUT"] = self._skipped_asset("XAUT", f"Bybit linear error: {e}")

        # Classical gold via Twelve Data
        try:
            gold_4h = await self._get_twelve_time_series("XAU/USD", "4h", 20)
            gold_1d = await self._get_twelve_time_series("XAU/USD", "1day", 20)
            result["GOLD"] = self._normalize_twelve_series("GOLD", gold_4h, gold_1d)
        except Exception as e:
            result["GOLD"] = self._skipped_asset("GOLD", f"Twelve Data error: {e}")

        # WTI intentionally skipped
        result["WTI"] = self._skipped_asset(
            "WTI",
            "Skipped in stable config: no reliable provider access configured",
        )

        return result
