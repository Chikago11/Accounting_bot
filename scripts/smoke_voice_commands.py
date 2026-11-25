"""Opt-in real Hermes intent checks. Never imports Sheets or sends Telegram messages."""

import argparse
import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.models import parse_result
from voice.providers import HermesBridge
from voice.settings import VoiceSettings


# All text and amounts below are synthetic fixtures.
CASES = [
    ("andrei_show", "Покажи бюджет Андрея", {}, [("andrei_show", None, None)]),
    ("max_show", "Сколько осталось в бюджете Максима?", {}, [("max_show", None, None)]),
    ("report", "Покажи отчёт за месяц", {}, [("report", None, None)]),
    ("andrei_prompt", "Добавить бюджет Андрея", {}, [("andrei_add_prompt", None, None)]),
    ("andrei_sub_prompt", "Вычесть из бюджета Андрея", {}, [("andrei_sub_prompt", None, None)]),
    ("max_add_prompt", "Добавить в бюджет Макса", {}, [("max_add_prompt", None, None)]),
    ("max_sub_prompt", "Вычесть из бюджета Макса", {}, [("max_sub_prompt", None, None)]),
    (
        "andrei_amount",
        "Пятьсот",
        {"mode": "add_amb", "menu": "andrei"},
        [("andrei_add", Decimal("500"), None)],
    ),
    (
        "andrei_add",
        "Добавь в бюджет Андрея пятьсот бат",
        {},
        [("andrei_add", Decimal("500"), None)],
    ),
    ("andrei_sub", "Вычти из бюджета Андрея сто бат", {}, [("andrei_sub", Decimal("100"), None)]),
    ("max_add", "Пополни бюджет Макса на триста бат", {}, [("max_add", Decimal("300"), None)]),
    ("max_sub", "Убери из бюджета Макса двести бат", {}, [("max_sub", Decimal("200"), None)]),
    (
        "both_budgets",
        "Покажи оба бюджета",
        {},
        [("andrei_show", None, None), ("max_show", None, None)],
    ),
    ("budgets_menu", "Открой отдельные бюджеты", {}, [("menu_budgets", None, None)]),
    ("andrei_menu", "Открой меню бюджета Андрея", {}, [("menu_andrei", None, None)]),
    ("max_menu", "Открой меню бюджета Макса", {}, [("menu_max", None, None)]),
    ("main_menu", "Главное меню", {}, [("menu_main", None, None)]),
    ("back", "Назад", {"menu": "max"}, [("menu_budgets", None, None)]),
    (
        "back_from_amount",
        "Назад",
        {"mode": "add", "category": "Супермаркет", "menu": "main"},
        [("expense_add_prompt", None, None)],
    ),
    ("expense_add_prompt", "Добавить расход", {}, [("expense_add_prompt", None, None)]),
    ("expense_sub_prompt", "Отменить расход", {}, [("expense_sub_prompt", None, None)]),
    (
        "expense_category",
        "Супермаркет",
        {"mode": "add", "menu": "main"},
        [("expense_add_prompt", None, "supermarket")],
    ),
    (
        "expense_amount",
        "Двести пятьдесят",
        {"mode": "add", "category": "Супермаркет", "menu": "main"},
        [("expense_add", Decimal("250"), "supermarket")],
    ),
    (
        "write_and_show",
        "Добавь Максу в бюджет триста бат и покажи его остаток",
        {},
        [("max_add", Decimal("300"), None), ("max_show", None, None)],
    ),
]


async def smoke(args):
    settings = replace(
        VoiceSettings(), bridge_command=tuple(json.loads(args.command)), hermes_model=args.model
    )
    bridge = HermesBridge(settings)
    selected = [case for case in CASES if not args.case or case[0] == args.case]
    if not selected or (args.audio and len(selected) != 1):
        raise ValueError("Choose one known --case when using --audio")
    for name, text, dialog, expected in selected:
        if args.audio:
            text = await bridge.transcribe(args.audio.read_bytes())
        operations, clarification = parse_result(await bridge.parse(text, dialog=dialog), settings)
        actual = [(op.action, op.amount, op.category_id) for op in operations]
        if clarification is not None or actual != expected:
            raise AssertionError(
                f"{name}: expected {expected}, got {actual}; clarification={clarification is not None}"
            )
        print(f"{name}: OK", flush=True)
    print(
        f"{len(selected)} real Hermes cases passed; financial writes: 0; Telegram messages: 0",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default=os.getenv("VOICE_BRIDGE_COMMAND"))
    parser.add_argument("--model", default=os.getenv("HERMES_MODEL", ""))
    parser.add_argument("--case", choices=[case[0] for case in CASES])
    parser.add_argument("--audio", type=Path, help="Synthetic audio for the selected case")
    args = parser.parse_args()
    if not args.command:
        parser.error("Set VOICE_BRIDGE_COMMAND or --command")
    asyncio.run(smoke(args))
