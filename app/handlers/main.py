from __future__ import annotations

from aiogram import Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.keyboards.main import build_skip_keyboard, build_use_case_keyboard
from app.notifications import NotificationService
from app.middlewares.submission_guard import SubmissionGuardMiddleware


class LeadForm(StatesGroup):
    name = State()
    use_case = State()
    custom_task = State()
    extra = State()


def register_handlers(dp: Dispatcher, notification_service: NotificationService) -> None:
    router = Router(name="main")
    guard_text = "Ваша заявка уже в обработке, с вами свяжутся в ближайшее время. Спасибо, что выбрали нас!"
    router.message.middleware(SubmissionGuardMiddleware(notification_service, guard_text))
    router.callback_query.middleware(SubmissionGuardMiddleware(notification_service, guard_text))

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Как к вам обращаться?", reply_markup=ReplyKeyboardRemove())
        await state.set_state(LeadForm.name)

    @router.message(LeadForm.name, F.text)
    async def process_name(message: Message, state: FSMContext) -> None:
        name = message.text.strip()
        await state.update_data(name=name)
        await message.answer(
            "Какая у вас задача?",
            reply_markup=build_use_case_keyboard(),
        )
        await state.set_state(LeadForm.use_case)

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
            await callback.message.answer("Опишите вашу задачу.")
            await state.set_state(LeadForm.custom_task)
        else:
            await _ask_additional_info(callback.message, state)

    @router.message(LeadForm.custom_task, F.text)
    async def process_custom_task(message: Message, state: FSMContext) -> None:
        await state.update_data(custom_task=message.text.strip(), use_case="Свой вариант")
        await _ask_additional_info(message, state)

    @router.message(LeadForm.extra, F.text)
    async def process_extra(message: Message, state: FSMContext) -> None:
        text = message.text.strip()
        skip = text.lower() == "пропустить"
        await state.update_data(extra=None if skip else text)
        await _finish_flow(message, state, notification_service)

    dp.include_router(router)


async def _ask_additional_info(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Дополнительная информация для нас. Отправьте сообщение или нажмите кнопку «Пропустить».",
        reply_markup=build_skip_keyboard(),
    )
    await state.set_state(LeadForm.extra)


async def _finish_flow(message: Message, state: FSMContext, notification_service: NotificationService) -> None:
    data = await state.update_data(
        telegram_id=message.from_user.id if message.from_user else None,
        username=message.from_user.username if message.from_user else None,
    )

    payload = {
        "name": data.get("name"),
        "use_case": data.get("use_case"),
        "custom_task": data.get("custom_task"),
        "extra": data.get("extra"),
        "telegram_id": data.get("telegram_id"),
        "username": data.get("username"),
    }

    await message.answer(
        "Ваша заявка принята и уже в обработке. В ближайшее время мы с вами свяжемся в личном чате. Спасибо, что выбрали нас!",
        reply_markup=ReplyKeyboardRemove(),
    )

    if notification_service:
        await notification_service.send_lead(payload)

    await state.clear()
