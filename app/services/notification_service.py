from __future__ import annotations

from sqlalchemy import select

from app.db.models import SignalEvent
from app.db.session import SessionLocal


class NotificationService:
    def was_sent(self, telegram_id: int, asset: str, event_code: str) -> bool:
        with SessionLocal() as db:
            row = db.scalar(
                select(SignalEvent).where(
                    SignalEvent.telegram_id == telegram_id,
                    SignalEvent.asset == asset,
                    SignalEvent.event_code == event_code,
                )
            )
            return row is not None

    def mark_sent(self, telegram_id: int, asset: str, event_code: str) -> None:
        with SessionLocal() as db:
            db.add(
                SignalEvent(
                    telegram_id=telegram_id,
                    asset=asset,
                    event_code=event_code,
                )
            )
            db.commit()
