from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from openai import AsyncOpenAI, RateLimitError

from app.core.config import settings
from app.schemas.signal import GenerateSignalsResponse


class AIService:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def fallback_signals(
        self,
        market_snapshot: list[dict[str, Any]],
        reason_text: str = "fallback mode",
    ) -> GenerateSignalsResponse:
        signals: list[dict[str, Any]] = []

        for item in market_snapshot:
            asset = item["asset"]
            price = float(item["price"])
            high_24h = float(item["high_24h"])
            low_24h = float(item["low_24h"])
            trend_4h = item["trend_4h"]
            trend_1d = item["trend_1d"]

            if price <= 0 or high_24h <= 0 or low_24h <= 0:
                continue

            price_range = max(high_24h - low_24h, price * 0.005)

            if trend_4h == "up" and trend_1d == "up":
                direction = "LONG"
                start_from = round(max(low_24h, price - price_range * 0.35), 5)
                start_to = round(price, 5)
                take_profit = round(price + price_range * 0.8, 5)
                stop_loss = round(start_from - price_range * 0.35, 5)
                priority = "A" if item["change_24h"] > 0 else "B"
                invalidation_rule = f"4H close below {stop_loss}"
            elif trend_4h == "down" and trend_1d == "down":
                direction = "SHORT"
                start_from = round(price, 5)
                start_to = round(min(high_24h, price + price_range * 0.35), 5)
                take_profit = round(price - price_range * 0.8, 5)
                stop_loss = round(start_to + price_range * 0.35, 5)
                priority = "A" if item["change_24h"] < 0 else "B"
                invalidation_rule = f"4H close above {stop_loss}"
            else:
                direction = "NO TRADE"
                start_from = 0.0
                start_to = 0.0
                take_profit = 0.0
                stop_loss = 0.0
                priority = "C"
                invalidation_rule = None

            signals.append(
                {
                    "asset": asset,
                    "direction": direction,
                    "start_from": start_from,
                    "start_to": start_to,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                    "priority": priority,
                    "status": "ACTIVE" if direction != "NO TRADE" else "CANCELLED",
                    "reason": reason_text,
                    "timeframe": "4H/1D",
                    "invalidation_rule": invalidation_rule,
                }
            )

        return GenerateSignalsResponse.model_validate({"signals": signals})

    async def generate_signals(self, market_snapshot: list[dict[str, Any]]) -> GenerateSignalsResponse:
        if not market_snapshot:
            return GenerateSignalsResponse.model_validate({"signals": []})

        system_prompt = """
You are a market signal engine for BTC, GOLD, SILVER, and WTI.

Rules:
- Use ONLY the provided market snapshot.
- Prefer actionable signals when the context is sufficient.
- Use NO TRADE only when the trends conflict or the setup is genuinely weak.
- NEVER return ACTIVE with zero levels.
- If direction is NO TRADE, then:
  - status must be CANCELLED
  - start_from, start_to, take_profit, stop_loss must all be 0
  - priority must be C
- If direction is LONG or SHORT, then:
  - status must be ACTIVE
  - all price levels must be > 0
  - take_profit and stop_loss must be logically placed relative to direction
- Allowed priorities: A, A-, B, C
- Allowed statuses: ACTIVE, CANCELLED, EXPIRED
- Return ONLY valid JSON.
""".strip()

        user_prompt = f"""
Market snapshot:
{json.dumps(market_snapshot, ensure_ascii=False)}

Return JSON exactly in this shape:
{{
  "signals": [
    {{
      "asset": "BTC",
      "direction": "LONG|SHORT|NO TRADE",
      "start_from": 0,
      "start_to": 0,
      "take_profit": 0,
      "stop_loss": 0,
      "priority": "A|A-|B|C",
      "status": "ACTIVE|CANCELLED|EXPIRED",
      "reason": "string",
      "timeframe": "4H/1D",
      "invalidation_rule": "string or null"
    }}
  ]
}}
""".strip()

        try:
            response = await self.client.responses.create(
                model="gpt-5.4",
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw_text = (response.output_text or "").strip()
            if not raw_text:
                return self.fallback_signals(
                    market_snapshot,
                    "fallback mode: empty OpenAI response",
                )

            data = json.loads(raw_text)
            validated = GenerateSignalsResponse.model_validate(data)

            cleaned = []
            for s in validated.signals:
                if s.direction == "NO TRADE":
                    s.status = "CANCELLED"
                    s.priority = "C"
                    s.start_from = 0.0
                    s.start_to = 0.0
                    s.take_profit = 0.0
                    s.stop_loss = 0.0
                    s.invalidation_rule = None
                cleaned.append(s.model_dump())

            return GenerateSignalsResponse.model_validate({"signals": cleaned})

        except RateLimitError:
            return self.fallback_signals(
                market_snapshot,
                "fallback mode: OpenAI quota exceeded",
            )
        except JSONDecodeError:
            return self.fallback_signals(
                market_snapshot,
                "fallback mode: OpenAI returned non-JSON",
            )
        except Exception as e:
            return self.fallback_signals(
                market_snapshot,
                f"fallback mode: {type(e).__name__}",
            )
