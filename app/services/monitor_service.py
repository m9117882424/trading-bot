from __future__ import annotations

from typing import Any

from aiogram import Bot

from app.services.market_data_service import MarketDataService
from app.services.notification_service import NotificationService
from app.services.signal_service import SignalService


class MonitorService:
    def __init__(self) -> None:
        self.market = MarketDataService()
        self.signals = SignalService()
        self.notifications = NotificationService()

    def _calc_r_multiple(self, signal, current_price: float) -> float | None:
        if signal.direction not in {"LONG", "SHORT"}:
            return None

        entry = (float(signal.start_from) + float(signal.start_to)) / 2
        stop = float(signal.stop_loss)

        if signal.direction == "LONG":
            risk = entry - stop
            progress = current_price - entry
        else:
            risk = stop - entry
            progress = entry - current_price

        if risk <= 0:
            return None

        return progress / risk

    def _event_from_signal(self, signal, current_price: float) -> tuple[str | None, str | None]:
        if signal.direction == "NO TRADE":
            return None, None

        start_from = float(signal.start_from)
        start_to = float(signal.start_to)
        take_profit = float(signal.take_profit)
        stop_loss = float(signal.stop_loss)

        if start_from <= current_price <= start_to:
            return "ENTRY_ZONE", "цена в зоне входа — можно открывать позицию по сценарию"

        if signal.direction == "LONG":
            if current_price <= stop_loss:
                return "STOP_HIT", "стоп достигнут — сценарий сломан"
            if current_price >= take_profit:
                return "TAKE_HIT", "тейк достигнут — можно фиксировать прибыль"
            if current_price > start_to:
                r = self._calc_r_multiple(signal, current_price)
            else:
                r = self._calc_r_multiple(signal, current_price)
        else:
            if current_price >= stop_loss:
                return "STOP_HIT", "стоп достигнут — сценарий сломан"
            if current_price <= take_profit:
                return "TAKE_HIT", "тейк достигнут — можно фиксировать прибыль"
            r = self._calc_r_multiple(signal, current_price)

        if r is None:
            return None, None
        if r >= 2.0:
            return "R_2", "достигнут 2R+ — фиксируй 50% и сопровождай остаток"
        if r >= 1.5:
            return "R_1_5", "достигнут 1.5R — фиксируй 25–30% и защищай позицию"
        if r >= 1.0:
            return "R_1", "достигнут 1R — двигай стоп в безубыток"
        if r >= 0.5:
            return "R_0_5", "позиция в плюсе, наблюдай; стоп пока не трогай"

        return None, None

    async def check_and_notify(self, bot: Bot, telegram_id: int, username: str | None = None) -> None:
        snapshot = await self.market.get_data()
        signals = await self.signals.get_signals()

        signal_by_asset = {s.asset: s for s in signals}

        for asset, data in snapshot.items():
            if not data.get("enabled"):
                continue

            signal = signal_by_asset.get(asset)
            if not signal:
                continue

            current_price = float(data["price"])
            event_code, recommendation = self._event_from_signal(signal, current_price)

            if not event_code or not recommendation:
                continue

            if self.notifications.was_sent(telegram_id, asset, event_code):
                continue

            text = (
                f"{asset}\n\n"
                f"Событие: {event_code}\n"
                f"Текущая цена: {current_price}\n"
                f"Рекомендация: {recommendation}"
            )

            await bot.send_message(telegram_id, text)
            self.notifications.mark_sent(telegram_id, asset, event_code)
