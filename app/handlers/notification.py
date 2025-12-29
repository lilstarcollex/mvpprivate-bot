from __future__ import annotations

import json
import os
import tempfile
from typing import Any, List, Optional

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from openpyxl import Workbook

from app.notifications import NotificationService

PAGE_SIZE = 5

STATUS_CODES = {
    "accepted": "Принята",
    "in_work": "В работе",
    "waiting": "Ожидание правок",
    "done": "Готово",
}

USER_STATUS_MESSAGES = {
    "accepted": "Ваш сервер прошёл проверку, теперь мы подбираем специалиста. Как только специалист начнёт работу, мы вас оповестим. Спасибо, что выбрали нас!",
    "in_work": "Специалист уже работает с вашим сервером, когда он будет готов, мы вас оповестим.",
    "waiting": "Ответьте специалисту в личном чате. Возможно нужно что-то уточнить или что-то пошло не так.",
    "done": "Ваш ВПН уже готов и ждёт вас. Наш специалист уже написал вам.",
}


class AdminStates(StatesGroup):
    search_query = State()


def register_notification_handlers(dp: Dispatcher, notification_service: NotificationService) -> None:
    router = Router(name="notification")

    # Публичный список
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

    # Админ-панель
    @router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext) -> None:
        if not _is_admin_chat(message, notification_service):
            return
        await state.clear()
        await message.answer("Админ-панель", reply_markup=_admin_menu())

    @router.callback_query(F.data == "admin:menu")
    async def admin_menu_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        await state.clear()
        await callback.message.answer("Админ-панель", reply_markup=_admin_menu())

    @router.callback_query(F.data == "admin:search")
    async def admin_search_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        await state.set_state(AdminStates.search_query)
        await callback.message.answer("Введите ID пользователя или @username для поиска.")

    @router.message(AdminStates.search_query, F.text)
    async def admin_search_query(message: Message, state: FSMContext) -> None:
        if not _is_admin_chat(message, notification_service):
            return
        query = message.text.strip()
        results = await notification_service.search_leads(query, limit=10)
        if not results:
            await message.answer("Ничего не найдено.")
            return
        lines = []
        buttons = []
        for lead in results:
            tg = lead.get("telegram_id") or "-"
            uname = f"@{lead['username']}" if lead.get("username") else "-"
            lines.append(f"#{lead['id']} | {uname} | {tg} | {lead.get('status') or '—'}")
            buttons.append([InlineKeyboardButton(text=f"Открыть #{lead['id']}", callback_data=f"admin:view:{lead['id']}")])
        kb = InlineKeyboardMarkup(
            inline_keyboard=buttons + [[InlineKeyboardButton(text="Назад", callback_data="admin:menu")]]
        )
        await message.answer("Результаты:\n" + "\n".join(lines), reply_markup=kb)
        await state.clear()

    @router.callback_query(F.data.startswith("admin:view:"))
    async def admin_view(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        lead_id = _parse_int(callback.data.split(":")[-1])
        if not lead_id:
            return
        lead = await notification_service.get_lead(lead_id)
        if not lead:
            await callback.message.answer("Заявка не найдена или уже удалена.")
            return
        text = _format_lead_detail(lead)
        kb = _lead_actions_keyboard(lead_id, lead.get("status"))
        await callback.message.answer(text, reply_markup=kb)

    @router.callback_query(F.data.startswith("admin:status:"))
    async def admin_status(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        parts = callback.data.split(":")
        if len(parts) < 3:
            return
        lead_id = _parse_int(parts[2]) if len(parts) == 4 else _parse_int(parts[1])
        status_code = parts[-1]
        if not lead_id or status_code not in STATUS_CODES:
            return
        updated = await notification_service.update_status(lead_id, STATUS_CODES[status_code])
        if not updated:
            await callback.message.answer("Не удалось обновить статус (заявка не найдена).")
            return
        text = _format_lead_detail(updated)
        kb = _lead_actions_keyboard(lead_id, updated.get("status"))
        await callback.message.answer(f"Статус обновлён на «{STATUS_CODES[status_code]}».")
        await callback.message.answer(text, reply_markup=kb)
        user_id = updated.get("telegram_id")
        if user_id:
            note = USER_STATUS_MESSAGES.get(status_code)
            if note:
                sent = await notification_service.send_user_notification(int(user_id), note)
                if not sent:
                    await callback.message.answer("Не удалось отправить уведомление пользователю (возможно, он не писал боту).")

    @router.callback_query(F.data.startswith("admin:delete:"))
    async def admin_delete(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        lead_id = _parse_int(callback.data.split(":")[-1])
        if not lead_id:
            return
        lead = await notification_service.delete_lead(lead_id)
        if not lead:
            await callback.message.answer("Заявка не найдена или уже удалена.")
            return
        await callback.message.answer(f"Заявка #{lead_id} удалена.")
        if lead.get("telegram_id"):
            await notification_service.send_user_notification(
                int(lead["telegram_id"]),
                "Ваша заявка была удалена администратором. Если нужна новая, заполните форму заново.",
            )

    @router.callback_query(F.data == "admin:all")
    async def admin_all(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        await _send_admin_page(callback.message, notification_service, page=0)

    @router.callback_query(F.data.startswith("admin:list:"))
    async def admin_paginate(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        raw = callback.data.split(":")[-1]
        try:
            page = int(raw)
        except ValueError:
            return
        await _send_admin_page(callback.message, notification_service, page=page)

    @router.callback_query(F.data == "admin:export")
    async def admin_export(callback: CallbackQuery) -> None:
        if not _is_admin_chat(callback.message, notification_service):
            return
        await callback.answer()
        await _export_excel(callback.message, notification_service)

    dp.include_router(router)


def _is_admin_chat(message: Optional[Message], notification_service: NotificationService) -> bool:
    return bool(message and message.chat and message.chat.id == notification_service.chat_id)


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
        protocols_display = _format_protocols(lead.get("protocols"))
        lines.append(
            "\n".join(
                [
                    f"{idx}. {lead.get('created_at', '')}",
                    f"Сценарий: {lead.get('scenario') or '—'}",
                    f"Имя: {lead.get('name') or '—'}",
                    f"Тип задачи: {lead.get('use_case') or '—'}",
                    f"Детали: {lead.get('custom_task') or '—'}",
                    f"Дополнительно: {lead.get('extra') or '—'}",
                    f"VPS IP: {lead.get('vps_ip') or '—'} | Домен: {lead.get('domain') or '—'}",
                    f"Протоколы: {protocols_display}",
                    f"Оплата: {lead.get('payment_status') or '—'}",
                    f"Статус: {lead.get('status') or '—'}",
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
            InlineKeyboardButton(text=f"{page + 1} / {total_pages}", callback_data=f"leads:{page}"),
            InlineKeyboardButton(text="⏭️ В конец", callback_data=f"leads:{total_pages - 1}"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data=f"leads:{prev_page}"),
            InlineKeyboardButton(text="Вперёд »", callback_data=f"leads:{next_page}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поиск пользователя", callback_data="admin:search")],
            [InlineKeyboardButton(text="Все заявки", callback_data="admin:all")],
            [InlineKeyboardButton(text="Сформировать таблицу", callback_data="admin:export")],
        ]
    )


async def _send_admin_page(message: Message, notification_service: NotificationService, page: int) -> None:
    total = await notification_service.count_leads()
    if total == 0:
        await message.answer("Заявок нет.")
        return
    total_pages = (total - 1) // PAGE_SIZE + 1
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE
    leads = await notification_service.fetch_leads(limit=PAGE_SIZE, offset=offset)

    lines = []
    buttons: List[List[InlineKeyboardButton]] = []
    for lead in leads:
        protocols_display = _format_protocols(lead.get("protocols"))
        lines.append(
            f"#{lead['id']} | {lead.get('name') or '—'} | {lead.get('status') or '—'} | {lead.get('scenario') or '—'} | {protocols_display}"
        )
        buttons.append([InlineKeyboardButton(text=f"Открыть #{lead['id']}", callback_data=f"admin:view:{lead['id']}")])

    pag_buttons = [
        [
            InlineKeyboardButton(text="⏮️", callback_data=f"admin:list:0"),
            InlineKeyboardButton(text="«", callback_data=f"admin:list:{max(page-1,0)}"),
            InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="admin:menu"),
            InlineKeyboardButton(text="»", callback_data=f"admin:list:{min(page+1,total_pages-1)}"),
            InlineKeyboardButton(text="⏭️", callback_data=f"admin:list:{total_pages-1}"),
        ]
    ]
    kb = InlineKeyboardMarkup(
        inline_keyboard=buttons + pag_buttons + [[InlineKeyboardButton(text="Назад", callback_data="admin:menu")]]
    )
    await message.answer("Все заявки:\n" + "\n".join(lines), reply_markup=kb)


def _format_protocols(value: Any) -> str:
    protocols = value
    if isinstance(protocols, str):
        try:
            protocols = json.loads(protocols)
        except Exception:
            protocols = [protocols]
    if protocols:
        return ", ".join(protocols)
    return "—"


def _format_lead_detail(lead: dict[str, Any]) -> str:
    protocols_display = _format_protocols(lead.get("protocols"))
    return "\n".join(
        [
            f"#{lead.get('id')} | {lead.get('created_at')}",
            f"Статус: {lead.get('status') or '—'}",
            f"Сценарий: {lead.get('scenario') or '—'}",
            f"Имя: {lead.get('name') or '—'}",
            f"Тип задачи: {lead.get('use_case') or '—'}",
            f"Детали: {lead.get('custom_task') or '—'}",
            f"Дополнительно: {lead.get('extra') or '—'}",
            f"TG: @{lead['username']}" if lead.get("username") else f"TG ID: {lead.get('telegram_id')}",
            f"VPS IP: {lead.get('vps_ip') or '—'}",
            f"Домен: {lead.get('domain') or '—'}",
            f"SSH: {lead.get('ssh_ok')}",
            f"Протоколы: {protocols_display}",
            f"Bot Token: {lead.get('bot_token') or '—'}",
            f"Оплата: {lead.get('payment_status') or '—'}",
            f"Payment URL: {lead.get('payment_url') or '—'}",
        ]
    )


def _lead_actions_keyboard(lead_id: int, current_status: Optional[str]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    status_buttons: List[InlineKeyboardButton] = []
    for code, label in STATUS_CODES.items():
        prefix = "✅ " if current_status == label else ""
        status_buttons.append(InlineKeyboardButton(text=prefix + label, callback_data=f"admin:status:{lead_id}:{code}"))
        if len(status_buttons) == 2:
            rows.append(status_buttons)
            status_buttons = []
    if status_buttons:
        rows.append(status_buttons)

    rows.append([InlineKeyboardButton(text="Удалить", callback_data=f"admin:delete:{lead_id}")])
    rows.append([InlineKeyboardButton(text="Назад в меню", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _export_excel(message: Message, notification_service: NotificationService) -> None:
    leads = await notification_service.fetch_all_leads()
    if not leads:
        await message.answer("Заявок нет для экспорта.")
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    headers = [
        "ID",
        "Created At",
        "Status",
        "Scenario",
        "Name",
        "Use Case",
        "Custom Task",
        "Extra",
        "Telegram ID",
        "Username",
        "VPS IP",
        "SSH OK",
        "Domain",
        "Protocols",
        "Bot Token",
        "Payment ID",
        "Payment URL",
        "Payment Status",
    ]
    ws.append(headers)

    for lead in leads:
        ws.append(
            [
                lead.get("id"),
                lead.get("created_at"),
                lead.get("status"),
                lead.get("scenario"),
                lead.get("name"),
                lead.get("use_case"),
                lead.get("custom_task"),
                lead.get("extra"),
                lead.get("telegram_id"),
                lead.get("username"),
                lead.get("vps_ip"),
                lead.get("ssh_ok"),
                lead.get("domain"),
                _format_protocols(lead.get("protocols")),
                lead.get("bot_token"),
                lead.get("payment_id"),
                lead.get("payment_url"),
                lead.get("payment_status"),
            ]
        )

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        wb.save(tmp.name)
        tmp_path = tmp.name

    await message.answer_document(FSInputFile(tmp_path, filename="leads.xlsx"))
    try:
        os.remove(tmp_path)
    except Exception:
        pass


def _parse_int(val: str) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


__all__ = ["register_notification_handlers"]
