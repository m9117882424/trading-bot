from __future__ import annotations

from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.core.config import settings
from app.services.market_data_service import MarketDataService
from app.services.signal_service import SignalService

router = Router()
signal_service = SignalService()
market_service = MarketDataService()


def _extract_source(data: dict) -> str:
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}
    source = context_4h.get("source") or context_1d.get("source")
    return str(source) if source else "unknown"


def _format_number(value: float) -> str:
    if value >= 1000:
        return f"{value:.2f}"
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _calc_rr(direction: str, start_from: float, start_to: float, take_profit: float, stop_loss: float) -> str:
    if direction not in {"LONG", "SHORT"}:
        return "—"

    entry = (start_from + start_to) / 2

    if direction == "LONG":
        risk = entry - stop_loss
        reward = take_profit - entry
    else:
        risk = stop_loss - entry
        reward = entry - take_profit

    if risk <= 0 or reward <= 0:
        return "—"

    rr = reward / risk
    return f"1:{rr:.2f}"


def _slope_direction(closes: list[float]) -> str:
    if len(closes) < 3:
        return "unknown"

    up_steps = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])

    if up_steps >= len(closes) - 2 and closes[-1] > closes[0]:
        return "up"
    if down_steps >= len(closes) - 2 and closes[-1] < closes[0]:
        return "down"

    delta = closes[-1] - closes[0]
    avg_price = mean(closes) if closes else 0
    if avg_price:
        change_pct = abs(delta) / avg_price * 100
        if change_pct < 0.25:
            return "range"

    if closes[-1] > closes[0]:
        return "slight_up"
    if closes[-1] < closes[0]:
        return "slight_down"

    return "range"


def _trend_label_from_context(context: dict, candle_trend: str) -> str:
    closes = context.get("recent_closes", []) or []
    slope = _slope_direction(closes)

    if candle_trend == "up" and slope in {"up", "slight_up"}:
        return "восходящий"
    if candle_trend == "down" and slope in {"down", "slight_down"}:
        return "нисходящий"
    if slope == "range":
        return "боковик"
    if candle_trend in {"up", "down"}:
        return "смешанный"
    return "неопределённый"


def _structure_strength(context: dict) -> str:
    closes = context.get("recent_closes", []) or []
    if len(closes) < 4:
        return "слабая"

    slope = _slope_direction(closes)

    if slope in {"up", "down"}:
        return "сильная"
    if slope in {"slight_up", "slight_down"}:
        return "умеренная"
    if slope == "range":
        return "слабая"

    return "слабая"


def _market_regime(signal, data: dict) -> str:
    trend_4h_raw = data.get("trend_4h", "unknown")
    trend_1d_raw = data.get("trend_1d", "unknown")
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}

    trend_4h = _trend_label_from_context(context_4h, trend_4h_raw)
    trend_1d = _trend_label_from_context(context_1d, trend_1d_raw)
    strength_4h = _structure_strength(context_4h)

    if signal.direction == "NO TRADE":
        if trend_4h != trend_1d:
            return "конфликт таймфреймов"
        if strength_4h == "слабая":
            return "вялый рынок / флет"
        return "нет явного преимущества"

    if trend_4h == "нисходящий" and trend_1d == "нисходящий":
        if "bounce" in (signal.reason or "").lower() or "откат" in (signal.reason or "").lower():
            return "откат в нисходящем тренде"
        return "продолжение нисходящего тренда"

    if trend_4h == "восходящий" and trend_1d == "восходящий":
        if "pullback" in (signal.reason or "").lower() or "откат" in (signal.reason or "").lower():
            return "откат в восходящем тренде"
        return "продолжение восходящего тренда"

    if trend_4h == "боковик" or trend_1d == "боковик":
        return "диапазон / флет"

    return "смешанный режим"


def _russian_explanation(signal, data: dict) -> str:
    trend_4h_raw = data.get("trend_4h", "unknown")
    trend_1d_raw = data.get("trend_1d", "unknown")
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}

    trend_4h = _trend_label_from_context(context_4h, trend_4h_raw)
    trend_1d = _trend_label_from_context(context_1d, trend_1d_raw)
    strength_4h = _structure_strength(context_4h)
    change_24h = float(data.get("change_24h", 0.0))

    if signal.direction == "NO TRADE":
        return (
            f"Сделка пропущена. На 4H рынок выглядит как '{trend_4h}', на 1D — '{trend_1d}'. "
            f"Сила структуры: {strength_4h}. Этого недостаточно для уверенного входа."
        )

    direction_ru = "лонг" if signal.direction == "LONG" else "шорт"

    return (
        f"Идея: {direction_ru}. "
        f"На 4H рынок выглядит как '{trend_4h}', на 1D — как '{trend_1d}'. "
        f"Сила структуры на 4H: {strength_4h}. "
        f"Изменение за 24 часа: {change_24h:.2f}%. "
        f"Сигнал рассчитан как сценарий с планом входа, стопа и тейка, а не как импульсивный вход по рынку."
    )


def _trend_summary(data: dict) -> tuple[str, str, str]:
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}

    trend_4h = _trend_label_from_context(context_4h, data.get("trend_4h", "unknown"))
    trend_1d = _trend_label_from_context(context_1d, data.get("trend_1d", "unknown"))
    strength = _structure_strength(context_4h)

    return trend_4h, trend_1d, strength


@router.message(Command("signals"))
async def signals(message: Message):
    try:
        snapshot = await market_service.get_data()
        generated_signals = await signal_service.get_signals()

        signal_by_asset = {s.asset: s for s in generated_signals}

        tz = ZoneInfo(settings.default_timezone)
        generated_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        active_blocks: list[str] = []
        skipped_blocks: list[str] = []

        for asset, data in snapshot.items():
            enabled = bool(data.get("enabled", False))
            source = _extract_source(data)

            if not enabled:
                reason = data.get("skip_reason", "Нет валидных данных")
                skipped_blocks.append(
                    "\n".join(
                        [
                            f"• {asset}",
                            f"  Источник: {source}",
                            f"  Причина пропуска: {reason}",
                        ]
                    )
                )
                continue

            signal = signal_by_asset.get(asset)
            if not signal:
                skipped_blocks.append(
                    "\n".join(
                        [
                            f"• {asset}",
                            f"  Источник: {source}",
                            "  Причина пропуска: сигнал не был сгенерирован",
                        ]
                    )
                )
                continue

            rr = _calc_rr(
                signal.direction,
                float(signal.start_from),
                float(signal.start_to),
                float(signal.take_profit),
                float(signal.stop_loss),
            )

            trend_4h, trend_1d, strength = _trend_summary(data)
            regime = _market_regime(signal, data)
            explanation_ru = _russian_explanation(signal, data)

            if signal.direction == "NO TRADE":
                block = "\n".join(
                    [
                        f"🔹 {signal.asset}",
                        f"Статус: {signal.status}",
                        f"Тренд 4H: {trend_4h}",
                        f"Тренд 1D: {trend_1d}",
                        f"Сила структуры: {strength}",
                        f"Режим рынка: {regime}",
                        f"Источник: {source}",
                        f"Причина: {signal.reason}",
                        f"Пояснение: {explanation_ru}",
                    ]
                )
            else:
                block = "\n".join(
                    [
                        f"🔹 {signal.asset}",
                        f"Направление: {signal.direction}",
                        f"Вход: {_format_number(float(signal.start_from))} – {_format_number(float(signal.start_to))}",
                        f"Тейк: {_format_number(float(signal.take_profit))}",
                        f"Стоп: {_format_number(float(signal.stop_loss))}",
                        f"RR: {rr}",
                        f"Приоритет: {signal.priority}",
                        f"Статус: {signal.status}",
                        f"Тренд 4H: {trend_4h}",
                        f"Тренд 1D: {trend_1d}",
                        f"Сила структуры: {strength}",
                        f"Режим рынка: {regime}",
                        f"Источник: {source}",
                        f"Причина: {signal.reason}",
                        f"Пояснение: {explanation_ru}",
                    ]
                )

            active_blocks.append(block)

        parts: list[str] = [
            "📈 Сигналы",
            f"Время генерации: {generated_at} ({settings.default_timezone})",
        ]

        if active_blocks:
            parts.append("")
            parts.append("\n\n".join(active_blocks))
        else:
            parts.append("")
            parts.append("Подходящих сигналов сейчас нет.")

        if skipped_blocks:
            parts.append("")
            parts.append("⏭️ Пропущенные активы")
            parts.append("\n\n".join(skipped_blocks))

        await message.answer("\n".join(parts).strip())

    except Exception as e:
        await message.answer(f"Ошибка при получении сигналов: {type(e).__name__}: {e}")
        raise
