from app.services.ai_service import AIService
from app.services.market_data_service import MarketDataService


class SignalService:
    def __init__(self):
        self.ai = AIService()
        self.market = MarketDataService()

    def _is_valid_asset(self, data: dict) -> bool:
        if not data:
            return False

        if float(data.get("price", 0)) <= 0:
            return False
        if float(data.get("high_24h", 0)) <= 0:
            return False
        if float(data.get("low_24h", 0)) <= 0:
            return False

        trend_4h = data.get("trend_4h")
        trend_1d = data.get("trend_1d")
        if trend_4h not in {"up", "down", "range"}:
            return False
        if trend_1d not in {"up", "down", "range"}:
            return False

        context_4h = data.get("context_4h", {})
        context_1d = data.get("context_1d", {})

        if isinstance(context_4h, dict) and context_4h.get("error"):
            return False
        if isinstance(context_1d, dict) and context_1d.get("error"):
            return False

        return True

    async def get_signals(self):
        snapshot = await self.market.get_data()

        payload = []
        for asset, data in snapshot.items():
            if not self._is_valid_asset(data):
                continue

            payload.append(
                {
                    "asset": asset,
                    "price": data["price"],
                    "change_24h": data["change_24h"],
                    "high_24h": data["high_24h"],
                    "low_24h": data["low_24h"],
                    "trend_4h": data["trend_4h"],
                    "trend_1d": data["trend_1d"],
                    "context_4h": data["context_4h"],
                    "context_1d": data["context_1d"],
                }
            )

        response = await self.ai.generate_signals(payload)
        return response.signals
