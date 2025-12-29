from __future__ import annotations

import json
import re
from typing import Any, Dict, Set

from aiogram import Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.keyboards import (
    ALL_PROTOCOLS,
    build_bot_token_keyboard,
    build_edit_fields_keyboard,
    build_edit_prompt_keyboard,
    build_inline_skip,
    build_payment_keyboard,
    build_protocols_keyboard,
    build_scenario_keyboard,
    build_use_case_keyboard,
)
from app.notifications import NotificationService
from app.payments import PaymentClient
from app.utils.net import extract_ipv4, resolve_domain
from app.utils.ssh import test_ssh_connection


TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{35}$")


class LeadForm(StatesGroup):
    scenario = State()
    name = State()
    use_case = State()
    custom_task = State()
    extra = State()
    vps_ip = State()
    vps_password = State()
    vps_domain = State()
    vps_protocols = State()
    vps_bot_token = State()


class EditLead(StatesGroup):
    choose_field = State()
    update_value = State()


def register_handlers(dp: Dispatcher, notification_service: NotificationService, payment_client: PaymentClient) -> None:
    router = Router(name="main")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        if message.from_user and await notification_service.has_lead(message.from_user.id):
            await _send_existing_notice(message)
            await state.clear()
            return
        await state.clear()
        await state.set_state(LeadForm.scenario)
        await message.answer(
            "Привет! Выберите сценарий:",
            reply_markup=build_scenario_keyboard(),
        )

    # --- Scenario selection ---
    @router.callback_query(LeadForm.scenario, F.data.startswith("scenario:"))
    async def process_scenario(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        choice = callback.data.split(":", 1)[1]
        if choice == "specialist":
            await state.update_data(scenario="Нужен специалист")
            await state.set_state(LeadForm.name)
            await callback.message.answer("Как вас зовут?")
        elif choice == "vps":
            await state.update_data(scenario="Есть VPS, нужен настройщик")
            await state.set_state(LeadForm.vps_ip)
            await callback.message.answer("Укажите IP сервера (пример: 123.123.123.123)")

    # --- Specialist flow ---
    @router.message(LeadForm.name, F.text)
    async def process_name(message: Message, state: FSMContext) -> None:
        name = message.text.strip()
        await state.update_data(name=name)
        await state.set_state(LeadForm.use_case)
        await message.answer(
            "Выберите вариант использования:",
            reply_markup=build_use_case_keyboard(),
        )

    @router.callback_query(LeadForm.use_case, F.data.startswith("use_case:"))
    async def process_use_case(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        _, choice = callback.data.split(":", 1)
        use_case_map = {
            "business": "Для бизнеса | Корпоративный",
            "personal": "Личный | Дом",
            "custom": "Свой вариант",
        }
        use_case = use_case_map.get(choice, "Свой вариант")
        await state.update_data(use_case=use_case)

        if choice == "custom":
            await state.set_state(LeadForm.custom_task)
            await callback.message.answer("Опишите задачу.")
        else:
            await _ask_additional_info(callback.message, state)

    @router.message(LeadForm.custom_task, F.text)
    async def process_custom_task(message: Message, state: FSMContext) -> None:
        await state.update_data(custom_task=message.text.strip(), use_case="Свой вариант")
        await _ask_additional_info(message, state)

    @router.message(LeadForm.extra, F.text)
    async def process_extra(message: Message, state: FSMContext) -> None:
        await state.update_data(extra=message.text.strip())
        await _finish_specialist_flow(message, state, notification_service)

    @router.callback_query(LeadForm.extra, F.data == "skip_extra")
    async def process_extra_skip(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.update_data(extra=None)
        await _finish_specialist_flow(callback.message, state, notification_service)

    # --- VPS flow ---
    @router.message(LeadForm.vps_ip, F.text)
    async def process_vps_ip(message: Message, state: FSMContext) -> None:
        ip = extract_ipv4(message.text)
        if not ip:
            await message.answer("IP некорректен. Пример: 123.123.123.123")
            return
        await state.update_data(vps_ip=ip)
        await state.set_state(LeadForm.vps_password)
        await message.answer(
            "Введите пароль root (используется только для проверки подключения по SSH).",
        )

    @router.message(LeadForm.vps_password, F.text)
    async def process_vps_password(message: Message, state: FSMContext) -> None:
        password = message.text.strip()
        data = await state.get_data()
        ip = data.get("vps_ip")
        ssh_ok = await test_ssh_connection(ip, password) if ip else False
        if not ssh_ok:
            await message.answer(
                "Не удалось подключиться по SSH. Проверьте IP/пароль и попробуйте снова.",
            )
            await state.clear()
            await state.set_state(LeadForm.vps_ip)
            await state.update_data(scenario=data.get("scenario"))
            await message.answer("Укажите IP сервера (пример: 123.123.123.123)")
            return

        await state.update_data(vps_password=password, ssh_ok=ssh_ok)
        await state.set_state(LeadForm.vps_domain)
        await message.answer("Укажите домен (если есть).")

    @router.message(LeadForm.vps_domain, F.text)
    async def process_vps_domain(message: Message, state: FSMContext) -> None:
        domain = message.text.strip()
        data = await state.get_data()
        ip = data.get("vps_ip")
        resolved_ip = await resolve_domain(domain) if domain else None
        if domain and (not ip or not resolved_ip or resolved_ip != ip):
            await message.answer(
                "Домен не указывает на ваш IP. Проверьте данные и введите IP заново.",
            )
            await state.clear()
            await state.set_state(LeadForm.vps_ip)
            await state.update_data(scenario=data.get("scenario"))
            await message.answer("Укажите IP сервера (пример: 123.123.123.123)")
            return

        await state.update_data(domain=domain if domain else None, resolved_ip=resolved_ip, selected_protocols=[])
        await state.set_state(LeadForm.vps_protocols)
        await message.answer(
            "Выберите протоколы (можно несколько):",
            reply_markup=build_protocols_keyboard(set()),
        )

    @router.callback_query(LeadForm.vps_protocols, F.data.startswith("proto:"))
    async def process_protocols(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        action = callback.data.split(":", 1)[1]
        data = await state.get_data()
        selected: Set[str] = set(data.get("selected_protocols") or [])

        if action == "finish":
            if not selected:
                await callback.message.answer("Нужно выбрать хотя бы один протокол.")
                return
            await state.update_data(selected_protocols=list(selected))
            await state.set_state(LeadForm.vps_bot_token)
            await callback.message.answer(
                "Пришлите BOT TOKEN вашего телеграм-бота (или нажмите пропустить).",
                reply_markup=build_bot_token_keyboard(),
            )
            return

        if action in ALL_PROTOCOLS:
            selected.add(action)
            await state.update_data(selected_protocols=list(selected))
            remaining_keyboard = build_protocols_keyboard(selected)
            await callback.message.answer(
                f"Добавлен {action}. Можно выбрать ещё или завершить.",
                reply_markup=remaining_keyboard,
            )

    @router.callback_query(LeadForm.vps_bot_token, F.data == "bot_token:skip")
    async def skip_bot_token(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.update_data(bot_token=None)
        await _handle_payment(callback.message, state, notification_service, payment_client)

    @router.message(LeadForm.vps_bot_token, F.text)
    async def process_bot_token(message: Message, state: FSMContext) -> None:
        token = message.text.strip()
        if not TOKEN_PATTERN.match(token):
            await message.answer(
                "BOT TOKEN некорректен. Пример: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
                reply_markup=build_bot_token_keyboard(),
            )
            return
        await state.update_data(bot_token=token)
        await _handle_payment(message, state, notification_service, payment_client)

    # --- Edit flow ---
    @router.callback_query(F.data == "edit:start")
    async def start_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not await notification_service.has_lead(callback.from_user.id):
            await callback.answer()
            await callback.message.answer("Заявка не найдена. Начните заново: /start")
            return
        await callback.answer()
        await state.set_state(EditLead.choose_field)
        await callback.message.answer("Что вы хотите изменить?", reply_markup=build_edit_fields_keyboard())

    @router.callback_query(EditLead.choose_field, F.data.startswith("edit:field:"))
    async def choose_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not await notification_service.has_lead(callback.from_user.id):
            await callback.answer()
            await callback.message.answer("Заявка не найдена. Начните заново: /start")
            return
        await callback.answer()
        field = callback.data.split(":")[-1]
        await state.update_data(edit_field=field)
        await state.set_state(EditLead.update_value)
        prompts = {
            "ip": "Отправьте IP, пароль и домен (каждый с новой строки). Домен можно пропустить.",
            "name": "Отправьте новое имя.",
            "token": "Отправьте новый BOT TOKEN.",
            "protocols": f"Перечислите протоколы через запятую. Доступно: {', '.join(ALL_PROTOCOLS)}.",
            "extra": "Отправьте новую дополнительную информацию.",
        }
        await callback.message.answer(prompts.get(field, "Отправьте новые данные."))

    @router.callback_query(F.data == "edit:cancel")
    async def cancel_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        await callback.message.answer("Изменение отменено.")

    @router.message(EditLead.update_value, F.text)
    async def apply_edit(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        field = data.get("edit_field")
        if not field:
            await state.clear()
            await _send_existing_notice(message)
            return

        if field == "ip":
            parts = [p.strip() for p in message.text.splitlines() if p.strip()]
            if len(parts) < 2:
                await message.answer("Укажите IP, пароль и домен (минимум IP и пароль) построчно.")
                return
            ip = extract_ipv4(parts[0])
            if not ip:
                await message.answer("IP указан некорректно. Пример: 123.123.123.123")
                return
            password = parts[1]
            domain = parts[2] if len(parts) > 2 else None
            ssh_ok = await test_ssh_connection(ip, password)
            updates = {"vps_ip": ip, "root_password": password, "domain": domain, "ssh_ok": ssh_ok}
        elif field == "name":
            updates = {"name": message.text.strip()}
        elif field == "token":
            token = message.text.strip()
            if not TOKEN_PATTERN.match(token):
                await message.answer("BOT TOKEN некорректен, попробуйте ещё раз.")
                return
            updates = {"bot_token": token}
        elif field == "protocols":
            protocols = _parse_protocols_text(message.text)
            if not protocols:
                await message.answer(f"Укажите хотя бы один из: {', '.join(ALL_PROTOCOLS)}")
                return
            updates = {"protocols": protocols}
        elif field == "extra":
            updates = {"extra": message.text.strip()}
        else:
            updates = {}

        await _apply_lead_updates(message, state, notification_service, updates)

    @router.message()
    async def existing_fallback(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        current_state = await state.get_state()
        if current_state and current_state.startswith(EditLead.__name__):
            return
        if await notification_service.has_lead(message.from_user.id):
            await _send_existing_notice(message)
            return

    dp.include_router(router)


async def _ask_additional_info(message: Message, state: FSMContext) -> None:
    await state.set_state(LeadForm.extra)
    await message.answer(
        "Добавьте дополнительную информацию (или пропустите).",
        reply_markup=build_inline_skip("skip_extra"),
    )


async def _finish_specialist_flow(message: Message, state: FSMContext, notification_service: NotificationService) -> None:
    user_payload = await _collect_user_payload(message)
    data = await state.update_data(**user_payload)

    payload = {
        "scenario": data.get("scenario") or "Нужен специалист",
        "name": data.get("name"),
        "use_case": data.get("use_case"),
        "custom_task": data.get("custom_task"),
        "extra": data.get("extra"),
        "status": "Принята",
        "payment_status": "pending",
        **user_payload,
    }

    await message.answer(
        "Заявка принята! Специалист свяжется с вами. Если нужно что-то изменить, используйте кнопку ниже.",
        reply_markup=build_edit_prompt_keyboard(),
    )

    await notification_service.send_lead(payload)
    await state.clear()


async def _handle_payment(
    message: Message,
    state: FSMContext,
    notification_service: NotificationService,
    payment_client: PaymentClient,
) -> None:
    user_payload = await _collect_user_payload(message)
    await state.update_data(**user_payload)
    data = await state.get_data()
    payment_result = await payment_client.create_payment(
        amount_rub=999,
        description="Настройка VPN",
        metadata={"telegram_id": data.get("telegram_id")},
    )
    await state.update_data(
        payment_id=payment_result.payment_id,
        payment_url=payment_result.payment_url,
        payment_status=payment_result.status,
    )

    await message.answer(
        "Счёт на оплату 999 ₽ сформирован.",
        reply_markup=build_payment_keyboard(payment_result.payment_url),
    )

    await _finish_vps_flow(message, state, notification_service)


async def _finish_vps_flow(message: Message, state: FSMContext, notification_service: NotificationService) -> None:
    user_payload = await _collect_user_payload(message)
    data = await state.update_data(**user_payload)

    payload = {
        "scenario": data.get("scenario") or "Есть VPS, нужен настройщик",
        "vps_ip": data.get("vps_ip"),
        "ssh_ok": data.get("ssh_ok"),
        "root_password": data.get("vps_password"),
        "domain": data.get("domain"),
        "protocols": data.get("selected_protocols") or [],
        "bot_token": data.get("bot_token"),
        "payment_id": data.get("payment_id"),
        "payment_url": data.get("payment_url"),
        "payment_status": data.get("payment_status"),
        "status": "Принята",
        **user_payload,
    }

    await message.answer(
        "Заявка принята! Специалист настроит VPN и уведомит вас. Если нужно поменять данные, нажмите «Изменить».",
        reply_markup=build_edit_prompt_keyboard(),
    )

    await notification_service.send_lead(payload)
    await state.clear()


async def _collect_user_payload(message: Message) -> Dict[str, Any]:
    """
    Собирает данные реального пользователя.
    Если по какой-то причине message.from_user — бот, пробуем get_chat для актуальных данных.
    """
    user_obj = message.from_user
    chat_obj = None
    if (not user_obj) or user_obj.is_bot:
        try:
            chat_obj = await message.bot.get_chat(message.chat.id)
            if chat_obj and getattr(chat_obj, "type", "") == "private":
                user_obj = chat_obj
        except Exception:
            user_obj = message.from_user

    if not user_obj:
        return {}

    try:
        raw = user_obj.model_dump()
    except Exception:
        raw = None

    return {
        "telegram_id": user_obj.id,
        "username": getattr(user_obj, "username", None),
        "first_name": getattr(user_obj, "first_name", None),
        "last_name": getattr(user_obj, "last_name", None),
        "language_code": getattr(user_obj, "language_code", None),
        "is_premium": getattr(user_obj, "is_premium", None),
        "is_bot": getattr(user_obj, "is_bot", None),
        "raw_user": raw,
        "user_json": json.dumps(raw, ensure_ascii=False) if raw else None,
    }


def _parse_protocols_text(text: str) -> list[str]:
    parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    result: list[str] = []
    for part in parts:
        for proto in ALL_PROTOCOLS:
            if part.lower() == proto.lower():
                if proto not in result:
                    result.append(proto)
    return result


async def _apply_lead_updates(message: Message, state: FSMContext, notification_service: NotificationService, updates: Dict[str, Any]) -> None:
    user_payload = await _collect_user_payload(message)
    telegram_id = user_payload.get("telegram_id")
    if not telegram_id:
        await message.answer("Не удалось определить пользователя.")
        return

    lead = await notification_service.update_lead_by_telegram(str(telegram_id), {**updates, **user_payload})
    if not lead:
        await message.answer("Заявка не найдена. Начните заново: /start")
        await state.clear()
        return

    await notification_service.send_lead({**lead, **user_payload})
    await message.answer(
        "Данные обновлены. Если хотите изменить что-то ещё, выберите пункт ниже.",
        reply_markup=build_edit_fields_keyboard(),
    )
    await state.set_state(EditLead.choose_field)


async def _send_existing_notice(message: Message) -> None:
    await message.answer(
        'Ваша заявка уже создана. Специалист уже настраивает ваш VPN. Если вы хотите изменить данные, нажмите на кнопку "Изменить".',
        reply_markup=build_edit_prompt_keyboard(),
    )


__all__ = ["register_handlers"]
