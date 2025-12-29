from __future__ import annotations

from typing import List, Sequence, Set

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_scenario_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Есть VPS, нужен настройщик", callback_data="scenario:vps")],
        [InlineKeyboardButton(text="Нужен специалист", callback_data="scenario:specialist")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_use_case_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="Для бизнеса | Корпоративный", callback_data="use_case:business"),
            InlineKeyboardButton(text="Личный | Дом", callback_data="use_case:personal"),
        ],
        [InlineKeyboardButton(text="Свой вариант", callback_data="use_case:custom")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_inline_skip(callback_data: str = "skip") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data=callback_data)]]
    )


ALL_PROTOCOLS: Sequence[str] = ("VLESS", "ShadowSocks", "VMESS", "Trojan", "WireGuard")


def build_protocols_keyboard(selected: Set[str]) -> InlineKeyboardMarkup:
    remaining = [p for p in ALL_PROTOCOLS if p not in selected]
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for proto in remaining:
        row.append(InlineKeyboardButton(text=proto, callback_data=f"proto:{proto}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Готово", callback_data="proto:finish")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_payment_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить 999 ₽", url=url)],
        ]
    )


def build_bot_token_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="bot_token:skip")],
        ]
    )


def build_edit_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить", callback_data="edit:start")],
        ]
    )


def build_edit_fields_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="IP, пароль и домен", callback_data="edit:field:ip")],
            [InlineKeyboardButton(text="Имя", callback_data="edit:field:name")],
            [InlineKeyboardButton(text="Токен бота", callback_data="edit:field:token")],
            [InlineKeyboardButton(text="Протоколы", callback_data="edit:field:protocols")],
            [InlineKeyboardButton(text="Дополнительную информацию", callback_data="edit:field:extra")],
            [InlineKeyboardButton(text="Отмена", callback_data="edit:cancel")],
        ]
    )


__all__ = [
    "build_scenario_keyboard",
    "build_use_case_keyboard",
    "build_inline_skip",
    "build_protocols_keyboard",
    "build_payment_keyboard",
    "build_bot_token_keyboard",
    "build_edit_prompt_keyboard",
    "build_edit_fields_keyboard",
    "ALL_PROTOCOLS",
]
