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


def _format_number(value: float) -> str:
    if value >= 1000:
        return f"{value:.2f}"
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _extract_source(data: dict) -> str:
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}
    source = context_4h.get("source") or context_1d.get("source")
    return str(source) if source else "unknown"


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

    return f"1:{reward / risk:.2f}"


def _calc_r_multiple(signal, current_price: float) -> tuple[str, float | None]:
    if signal.direction not in {"LONG", "SHORT"}:
        return "—", None

    entry = (float(signal.start_from) + float(signal.start_to)) / 2
    stop = float(signal.stop_loss)

    if signal.direction == "LONG":
        risk = entry - stop
        progress = current_price - entry
    else:
        risk = stop - entry
        progress = entry - current_price

    if risk <= 0:
        return "—", None

    r_value = progress / risk
    return f"{r_value:.2f}R", r_value


def _r_action_by_multiple(r_value: float | None) -> tuple[str, str]:
    if r_value is None:
        return "нет данных", "Невозможно оценить управление позицией"

    if r_value < 0:
        return "сценарий не подтверждён", "Держи только если цена всё ещё в рамках исходного плана"
    if r_value < 0.5:
        return "держи", "Позиция ещё не дала достаточного запаса хода"
    if r_value < 1.0:
        return "держи", "Сценарий развивается, но стоп пока рано переносить"
    if r_value < 1.5:
        return "двигай стоп", "Можно рассмотреть перенос стопа в безубыток"
    if r_value < 2.0:
        return "фиксируй часть", "Есть смысл закрыть часть позиции и защитить остаток"
    return "фиксируй часть", "Можно частично фиксировать прибыль и сопровождать остаток по тренду"


def _slope_direction(closes: list[float]) -> str:
    if len(closes) < 3:
        return "unknown"

    up_steps = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])

    if up_steps >= len(closes) - 2 and closes[-1] > closes[0]:
        return "up"
    if down_steps >= len(closes) - 2 and closes[-1] < closes[0]:
        return "down"

    avg_price = mean(closes) if closes else 0
    delta = closes[-1] - closes[0]
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
    reason = (signal.reason or "").lower()

    if signal.direction == "NO TRADE":
        if trend_4h != trend_1d:
            return "конфликт таймфреймов"
        if strength_4h == "слабая":
            return "вялый рынок / флет"
        return "нет явного преимущества"

    if trend_4h == "нисходящий" and trend_1d == "нисходящий":
        if "bounce" in reason or "откат" in reason or "weak bounces" in reason:
            return "откат в нисходящем тренде"
        return "продолжение нисходящего тренда"

    if trend_4h == "восходящий" and trend_1d == "восходящий":
        if "pullback" in reason or "откат" in reason:
            return "откат в восходящем тренде"
        return "продолжение восходящего тренда"

    if trend_4h == "боковик" or trend_1d == "боковик":
        return "диапазон / флет"

    return "смешанный режим"


def _trend_summary(data: dict) -> tuple[str, str, str]:
    context_4h = data.get("context_4h", {}) or {}
    context_1d = data.get("context_1d", {}) or {}

    trend_4h = _trend_label_from_context(context_4h, data.get("trend_4h", "unknown"))
    trend_1d = _trend_label_from_context(context_1d, data.get("trend_1d", "unknown"))
    strength = _structure_strength(context_4h)

    return trend_4h, trend_1d, strength


def _position_status(signal, current_price: float) -> tuple[str, str]:
    if signal.direction == "NO TRADE":
        return "ОТМЕНЁН", "Сделка не рассматривается"

    start_from = float(signal.start_from)
    start_to = float(signal.start_to)
    take_profit = float(signal.take_profit)
    stop_loss = float(signal.stop_loss)

    zone_width = abs(start_to - start_from)
    if zone_width <= 0:
        zone_width = max(abs((start_from + start_to) / 2) * 0.003, 0.0001)

    distance_from_zone = 0.0
    if current_price < start_from:
        distance_from_zone = start_from - current_price
    elif current_price > start_to:
        distance_from_zone = current_price - start_to

    far_multiplier = 1.5
    stale_multiplier = 3.0

    in_entry_zone = start_from <= current_price <= start_to

    if signal.direction == "LONG":
        if current_price <= stop_loss:
            return "СТОП ДОСТИГНУТ", "Не входить / если вошёл — сценарий сломан"
        if current_price >= take_profit:
            return "ТЕЙК ДОСТИГНУТ", "Фиксировать прибыль / новый вход не искать"
        if in_entry_zone:
            return "АКТИВИРОВАН", "Цена в зоне входа — сценарий активен"
        if current_price > start_to:
            if distance_from_zone >= zone_width * stale_multiplier:
                return "СИГНАЛ УСТАРЕЛ", "Сценарий уже неактуален, нужен новый сетап"
            if distance_from_zone >= zone_width * far_multiplier:
                return "ВХОД ПРОПУЩЕН", "Цена уже ушла выше зоны, догонять движение не стоит"
            return "ВНЕ ЗОНЫ ВЫШЕ", "Ждать откат к зоне входа"
        if current_price < start_from and distance_from_zone >= zone_width * stale_multiplier:
            return "ЦЕНА СЛИШКОМ ДАЛЕКО ОТ СЦЕНАРИЯ", "Сценарий деформирован, лучше ждать новый сигнал"
        return "ОЖИДАНИЕ", "Цена ещё не дошла до зоны входа"

    if signal.direction == "SHORT":
        if current_price >= stop_loss:
            return "СТОП ДОСТИГНУТ", "Не входить / если вошёл — сценарий сломан"
        if current_price <= take_profit:
            return "ТЕЙК ДОСТИГНУТ", "Фиксировать прибыль / новый вход не искать"
        if in_entry_zone:
            return "АКТИВИРОВАН", "Цена в зоне входа — сценарий активен"
        if current_price < start_from:
            if distance_from_zone >= zone_width * stale_multiplier:
                return "СИГНАЛ УСТАРЕЛ", "Сценарий уже неактуален, нужен новый сетап"
            if distance_from_zone >= zone_width * far_multiplier:
                return "ВХОД ПРОПУЩЕН", "Цена уже ушла ниже зоны, шортить внизу не стоит"
            return "ВНЕ ЗОНЫ НИЖЕ", "Ждать откат к зоне входа"
        if current_price > start_to and distance_from_zone >= zone_width * stale_multiplier:
            return "ЦЕНА СЛИШКОМ ДАЛЕКО ОТ СЦЕНАРИЯ", "Сценарий деформирован, лучше ждать новый сигнал"
        return "ОЖИДАНИЕ", "Цена ещё не дошла до зоны входа"

    return "НЕИЗВЕСТНО", "Нет понятного сценария"


def _russian_status_explanation(signal, data: dict, pos_status: str) -> str:
    trend_4h, trend_1d, strength = _trend_summary(data)
    regime = _market_regime(signal, data)

    if signal.direction == "NO TRADE":
        return (
            f"Сценарий отменён. На 4H рынок выглядит как '{trend_4h}', на 1D — '{trend_1d}'. "
            f"Сила структуры: {strength}. Режим рынка: {regime}."
        )

    if pos_status == "СИГНАЛ УСТАРЕЛ":
        return (
            f"Сигнал потерял актуальность. На 4H рынок выглядит как '{trend_4h}', на 1D — '{trend_1d}'. "
            f"Сила структуры: {strength}. Режим рынка: {regime}. Текущий вход уже не соответствует исходной идее."
        )

    if pos_status == "ВХОД ПРОПУЩЕН":
        return (
            f"Рынок уже ушёл без нормального входа. На 4H рынок выглядит как '{trend_4h}', на 1D — '{trend_1d}'. "
            f"Сила структуры: {strength}. Режим рынка: {regime}. Догонять цену не стоит."
        )

    if pos_status == "ЦЕНА СЛИШКОМ ДАЛЕКО ОТ СЦЕНАРИЯ":
        return (
            f"Цена слишком далеко отклонилась от исходной зоны. На 4H рынок выглядит как '{trend_4h}', на 1D — '{trend_1d}'. "
            f"Сила структуры: {strength}. Режим рынка: {regime}. Исходная геометрия сделки ухудшилась."
        )

    return (
        f"Сценарий сейчас в статусе '{pos_status}'. "
        f"На 4H рынок выглядит как '{trend_4h}', на 1D — как '{trend_1d}'. "
        f"Сила структуры: {strength}. Режим рынка: {regime}."
    )


@router.message(Command("status"))
async def status(message: Message):
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
                skipped_blocks.append(
                    "\n".join(
                        [
                            f"• {asset}",
                            f"  Источник: {source}",
                            f"  Причина пропуска: {data.get('skip_reason', 'Нет валидных данных')}",
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
                            "  Причина пропуска: нет активного сигнала для проверки",
                        ]
                    )
                )
                continue

            current_price = float(data["price"])
            rr = _calc_rr(
                signal.direction,
                float(signal.start_from),
                float(signal.start_to),
                float(signal.take_profit),
                float(signal.stop_loss),
            )
            r_multiple_text, r_multiple_value = _calc_r_multiple(signal, current_price)
            manage_action, manage_comment = _r_action_by_multiple(r_multiple_value)

            pos_status, action = _position_status(signal, current_price)
            trend_4h, trend_1d, strength = _trend_summary(data)
            regime = _market_regime(signal, data)
            explanation_ru = _russian_status_explanation(signal, data, pos_status)

            if signal.direction == "NO TRADE":
                block = "\n".join(
                    [
                        f"🔹 {signal.asset}",
                        f"Текущая цена: {_format_number(current_price)}",
                        f"Статус сценария: ОТМЕНЁН",
                        f"Тренд 4H: {trend_4h}",
                        f"Тренд 1D: {trend_1d}",
                        f"Сила структуры: {strength}",
                        f"Режим рынка: {regime}",
                        f"Источник: {source}",
                        f"Причина: {signal.reason}",
                        "Действие: пропустить, торгового преимущества нет",
                        f"Пояснение: {explanation_ru}",
                    ]
                )
            else:
                block = "\n".join(
                    [
                        f"🔹 {signal.asset}",
                        f"Направление: {signal.direction}",
                        f"Текущая цена: {_format_number(current_price)}",
                        f"Зона входа: {_format_number(float(signal.start_from))} – {_format_number(float(signal.start_to))}",
                        f"Тейк: {_format_number(float(signal.take_profit))}",
                        f"Стоп: {_format_number(float(signal.stop_loss))}",
                        f"RR: {rr}",
                        f"R-мультипликатор: {r_multiple_text}",
                        f"Статус сценария: {pos_status}",
                        f"Тренд 4H: {trend_4h}",
                        f"Тренд 1D: {trend_1d}",
                        f"Сила структуры: {strength}",
                        f"Режим рынка: {regime}",
                        f"Действие по сценарию: {action}",
                        f"Действие по позиции: {manage_action}",
                        f"Комментарий по позиции: {manage_comment}",
                        f"Источник: {source}",
                        f"Причина сигнала: {signal.reason}",
                        f"Пояснение: {explanation_ru}",
                    ]
                )

            active_blocks.append(block)

        parts: list[str] = [
            "📊 Статус сигналов",
            f"Время проверки: {generated_at} ({settings.default_timezone})",
        ]

        if active_blocks:
            parts.append("")
            parts.append("\n\n".join(active_blocks))
        else:
            parts.append("")
            parts.append("Сценариев для проверки сейчас нет.")

        if skipped_blocks:
            parts.append("")
            parts.append("⏭️ Пропущенные активы")
            parts.append("\n\n".join(skipped_blocks))

        await message.answer("\n".join(parts).strip())

    except Exception as e:
        await message.answer(f"Ошибка при проверке статусов: {type(e).__name__}: {e}")
        raise
