from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def build_use_case_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing a task type."""
    buttons = [
        [
            InlineKeyboardButton(text="Для бизнеса | Корпоративный", callback_data="use_case:business"),
            InlineKeyboardButton(text="Личное использование", callback_data="use_case:personal"),
        ],
        [InlineKeyboardButton(text="Свой вариант", callback_data="use_case:custom")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_skip_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard for skipping optional input."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


__all__ = ["build_skip_keyboard", "build_use_case_keyboard"]
