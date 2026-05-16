import asyncio

from aiogram import Bot, Dispatcher

from app.bot.handlers.signals import router as signals_router
from app.bot.handlers.status import router as status_router
from app.core.config import settings
from app.services.scheduler_service import setup_scheduler


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    dp.include_router(signals_router)
    dp.include_router(status_router)

    # замени на свой telegram_id
    setup_scheduler(bot, telegram_id=42707740)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
