from __future__ import annotations

from typing import Iterable, List, Sequence, Set

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_scenario_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="У меня уже есть VPS и домен", callback_data="scenario:vps")],
        [InlineKeyboardButton(text="Нужен специалист", callback_data="scenario:specialist")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_use_case_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="Для бизнеса | Корпоративный", callback_data="use_case:business"),
            InlineKeyboardButton(text="Личное использование", callback_data="use_case:personal"),
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
    buttons.append([InlineKeyboardButton(text="Закончить выбор", callback_data="proto:finish")])
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


__all__ = [
    "build_scenario_keyboard",
    "build_use_case_keyboard",
    "build_inline_skip",
    "build_protocols_keyboard",
    "build_payment_keyboard",
    "build_bot_token_keyboard",
    "ALL_PROTOCOLS",
]
