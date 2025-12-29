from __future__ import annotations

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
    build_inline_skip,
    build_payment_keyboard,
    build_protocols_keyboard,
    build_scenario_keyboard,
    build_use_case_keyboard,
)
from app.middlewares.submission_guard import SubmissionGuardMiddleware
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


def register_handlers(dp: Dispatcher, notification_service: NotificationService, payment_client: PaymentClient) -> None:
    router = Router(name="main")
    guard_text = "Ваш VPN уже в работе. Если появились вопросы или пожелания, напишите специалисту: @mvpvpnprivatespec. Спасибо, что выбрали нас!"
    router.message.middleware(SubmissionGuardMiddleware(notification_service, guard_text))
    router.callback_query.middleware(SubmissionGuardMiddleware(notification_service, guard_text))

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(LeadForm.scenario)
        await message.answer(
            "Привет! Выберите подходящий вариант:",
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
            await callback.message.answer("Как к вам обращаться?")
        elif choice == "vps":
            await state.update_data(scenario="Есть VPS и домен")
            await state.set_state(LeadForm.vps_ip)
            await callback.message.answer("Отправьте сообщением IP адрес сервера, в формате: 123.123.123.123")

    # --- Specialist flow ---
    @router.message(LeadForm.name, F.text)
    async def process_name(message: Message, state: FSMContext) -> None:
        name = message.text.strip()
        await state.update_data(name=name)
        await state.set_state(LeadForm.use_case)
        await message.answer(
            "Какая у вас задача?",
            reply_markup=build_use_case_keyboard(),
        )

    @router.callback_query(LeadForm.use_case, F.data.startswith("use_case:"))
    async def process_use_case(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        _, choice = callback.data.split(":", 1)
        use_case_map = {
            "business": "Для бизнеса | Корпоративный",
            "personal": "Личное использование",
            "custom": "Свой вариант",
        }
        use_case = use_case_map.get(choice, "Свой вариант")
        await state.update_data(use_case=use_case)

        if choice == "custom":
            await state.set_state(LeadForm.custom_task)
            await callback.message.answer("Опишите вашу задачу.")
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
            await message.answer("Не вижу корректный IPv4 адрес. Отправьте IP в формате 123.123.123.123")
            return
        await state.update_data(vps_ip=ip)
        await state.set_state(LeadForm.vps_password)
        await message.answer(
            "Отправьте пароль от root, он нужен будет специалисту для подключения и настройки сервера и VPN."
        )

    @router.message(LeadForm.vps_password, F.text)
    async def process_vps_password(message: Message, state: FSMContext) -> None:
        password = message.text.strip()
        data = await state.get_data()
        ip = data.get("vps_ip")
        ssh_ok = await test_ssh_connection(ip, password) if ip else False
        if not ssh_ok:
            await message.answer(
                "Не удалось подключиться по SSH с этими данными. Проверьте IP/пароль и отправьте IP снова."
            )
            await state.clear()
            await state.set_state(LeadForm.vps_ip)
            await state.update_data(scenario=data.get("scenario"))
            await message.answer("Отправьте IP адрес сервера, в формате: 123.123.123.123")
            return

        await state.update_data(vps_password=password, ssh_ok=ssh_ok)
        await state.set_state(LeadForm.vps_domain)
        await message.answer("Отправьте ваш домен.")

    @router.message(LeadForm.vps_domain, F.text)
    async def process_vps_domain(message: Message, state: FSMContext) -> None:
        domain = message.text.strip()
        data = await state.get_data()
        ip = data.get("vps_ip")
        resolved_ip = await resolve_domain(domain)
        if not ip or not resolved_ip or resolved_ip != ip:
            await message.answer(
                "Домен или IP указаны неверно, перепроверьте и отправьте данные снова.\n"
                "Сначала IP, затем пароль root, затем домен."
            )
            await state.clear()
            await state.set_state(LeadForm.vps_ip)
            await state.update_data(scenario=data.get("scenario"))
            await message.answer("Отправьте IP адрес сервера, в формате: 123.123.123.123")
            return

        await state.update_data(domain=domain, resolved_ip=resolved_ip, selected_protocols=[])
        await state.set_state(LeadForm.vps_protocols)
        await message.answer(
            "Данные прошли первичную проверку. Выберите протоколы:",
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
                await callback.message.answer("Выберите хотя бы один протокол перед завершением.")
                return
            await state.update_data(selected_protocols=list(selected))
            await state.set_state(LeadForm.vps_bot_token)
            await callback.message.answer(
                "Есть возможность управлять VPN через личный TG-бот. "
                "Отправьте BOT TOKEN или нажмите «Пропустить».\n"
                "Инструкция по созданию бота: https://core.telegram.org/bots#6-botfather",
                reply_markup=build_bot_token_keyboard(),
            )
            return

        if action in ALL_PROTOCOLS:
            selected.add(action)
            await state.update_data(selected_protocols=list(selected))
            remaining_keyboard = build_protocols_keyboard(selected)
            await callback.message.answer(
                f"Вы выбрали протокол {action}. Вы можете выбрать ещё или закончить выбор.",
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
                "Это не похоже на BOT TOKEN. Отправьте корректный токен вида 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 "
                "или нажмите «Пропустить».",
                reply_markup=build_bot_token_keyboard(),
            )
            return
        await state.update_data(bot_token=token)
        await _handle_payment(message, state, notification_service, payment_client)

    dp.include_router(router)


async def _ask_additional_info(message: Message, state: FSMContext) -> None:
    await state.set_state(LeadForm.extra)
    await message.answer(
        "Дополнительная информация для нас. Отправьте сообщение или нажмите «Пропустить».",
        reply_markup=build_inline_skip("skip_extra"),
    )


async def _finish_specialist_flow(message: Message, state: FSMContext, notification_service: NotificationService) -> None:
    data = await state.update_data(
        telegram_id=message.from_user.id if message.from_user else None,
        username=message.from_user.username if message.from_user else None,
    )

    payload = {
        "scenario": data.get("scenario") or "Нужен специалист",
        "name": data.get("name"),
        "use_case": data.get("use_case"),
        "custom_task": data.get("custom_task"),
        "extra": data.get("extra"),
        "telegram_id": data.get("telegram_id"),
        "username": data.get("username"),
        "status": "Принята",
        "payment_status": "pending",
    }

    await message.answer(
        "Ваша заявка принята и уже в обработке. В ближайшее время мы с вами свяжемся в личном чате. Спасибо, что выбрали нас!",
    )

    await notification_service.send_lead(payload)
    await state.clear()


async def _handle_payment(
    message: Message,
    state: FSMContext,
    notification_service: NotificationService,
    payment_client: PaymentClient,
) -> None:
    await state.update_data(
        telegram_id=message.from_user.id if message.from_user else None,
        username=message.from_user.username if message.from_user else None,
    )
    data = await state.get_data()
    payment_result = await payment_client.create_payment(
        amount_rub=999,
        description="Оплата настройки VPN",
        metadata={"telegram_id": data.get("telegram_id")},
    )
    await state.update_data(
        payment_id=payment_result.payment_id,
        payment_url=payment_result.payment_url,
        payment_status=payment_result.status,
    )

    await message.answer(
        "Специалист начнёт работу, сразу после оплаты. Цена услуги 999 ₽.\n"
        "Нажмите на кнопку, чтобы оплатить.",
        reply_markup=build_payment_keyboard(payment_result.payment_url),
    )

    await _finish_vps_flow(message, state, notification_service)


async def _finish_vps_flow(message: Message, state: FSMContext, notification_service: NotificationService) -> None:
    data = await state.update_data(
        telegram_id=message.from_user.id if message.from_user else None,
        username=message.from_user.username if message.from_user else None,
    )

    payload = {
        "scenario": data.get("scenario") or "Есть VPS и домен",
        "telegram_id": data.get("telegram_id"),
        "username": data.get("username"),
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
    }

    await message.answer(
        "Данные прошли проверку. Специалист уже занимается вашим сервером. "
        "Ничего не делайте с сервером до окончания работ (не выключайте/не перезагружайте). "
        "По завершению пришлём инструкцию по VPN и поможем сменить пароль.",
    )

    await notification_service.send_lead(payload)
    await state.clear()
