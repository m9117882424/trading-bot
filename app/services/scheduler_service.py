from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from app.core.config import settings
from app.services.monitor_service import MonitorService

scheduler = AsyncIOScheduler(timezone=settings.default_timezone)
monitor_service = MonitorService()


def setup_scheduler(bot: Bot, telegram_id: int) -> None:
    # только мониторинг, без morning/evening
    scheduler.add_job(
        monitor_service.check_and_notify,
        "interval",
        minutes=20,
        args=[bot, telegram_id],
        id=f"monitor_{telegram_id}",
        replace_existing=True,
    )

    if not scheduler.running:
        scheduler.start()
