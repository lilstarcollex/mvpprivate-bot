from __future__ import annotations

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.notifications import NotificationService

PAGE_SIZE = 5


def register_notification_handlers(dp: Dispatcher, notification_service: NotificationService) -> None:
    router = Router(name="notification")

    @router.message(Command("all"))
    async def cmd_all(message: Message) -> None:
        await _send_page(message, notification_service, page=0, edit=False)

    @router.callback_query(F.data.startswith("leads:"))
    async def paginate(callback: CallbackQuery) -> None:
        await callback.answer()
        parts = callback.data.split(":")
        if len(parts) != 2:
            return
        try:
            page = int(parts[1])
        except ValueError:
            return
        await _send_page(callback.message, notification_service, page=page, edit=True)

    dp.include_router(router)


async def _send_page(message: Message, notification_service: NotificationService, page: int, edit: bool) -> None:
    total = await notification_service.count_leads()
    if total == 0:
        await message.answer("История заявок пуста.")
        return

    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE
    leads = await notification_service.fetch_leads(limit=PAGE_SIZE, offset=offset)

    lines = []
    base_index = offset + 1
    for idx, lead in enumerate(leads, start=base_index):
        lines.append(
            "\n".join(
                [
                    f"{idx}. {lead['created_at']}",
                    f"Имя: {lead.get('name') or '—'}",
                    f"Тип задачи: {lead.get('use_case') or '—'}",
                    f"Детали: {lead.get('custom_task') or '—'}",
                    f"Дополнительно: {lead.get('extra') or '—'}",
                    f"TG: @{lead['username']}" if lead.get("username") else f"TG ID: {lead.get('telegram_id')}",
                ]
            )
        )

    text = f"Заявки (страница {page + 1}/{total_pages}):\n\n" + "\n\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…"

    keyboard = _build_pagination_keyboard(page, total_pages)

    if edit and message:
        await message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
    else:
        await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


def _build_pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    prev_page = max(page - 1, 0)
    next_page = min(page + 1, total_pages - 1)

    rows = [
        [
            InlineKeyboardButton(text="⏮️ В начало", callback_data="leads:0"),
            InlineKeyboardButton(text=f"{page + 1} / {total_pages}", callback_data="leads:{page}"),
            InlineKeyboardButton(text="⏭️ В конец", callback_data=f"leads:{total_pages - 1}"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data=f"leads:{prev_page}"),
            InlineKeyboardButton(text="Вперёд »", callback_data=f"leads:{next_page}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = ["register_notification_handlers"]
