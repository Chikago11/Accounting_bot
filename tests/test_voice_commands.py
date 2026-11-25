import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import main
from catalog import NAVIGATION_ACTIONS, READ_ACTIONS, VOICE_MENU_ACTIONS
from transactions import execute_operation
from voice.handler import VoiceController
from voice.models import parse_result, ValidationError
from voice.providers import HermesBridge
from voice.settings import VoiceSettings
from voice.store import PendingStore


def command(action, **values):
    return {
        "action": action,
        "amount": None,
        "currency": "THB",
        "category_id": None,
        "occurred_at": None,
    } | values


def payload(*items):
    return json.dumps({"operations": list(items), "clarification": None})


class CommandSchemaTests(unittest.TestCase):
    def test_every_menu_target_has_a_voice_action(self):
        targets = {target for _, buttons in main.MENUS.values() for _, target in buttons}
        self.assertEqual(targets, set(VOICE_MENU_ACTIONS.values()))

    def test_all_read_and_navigation_actions_are_valid_without_amount(self):
        for action in READ_ACTIONS | NAVIGATION_ACTIONS:
            with self.subTest(action=action):
                operations, question = parse_result(payload(command(action)), VoiceSettings())
                self.assertIsNone(question)
                self.assertFalse(operations[0].is_write)
                self.assertIsNone(operations[0].to_dict()["amount"])

    def test_reads_and_writes_can_share_a_confirmation_package(self):
        operations, _ = parse_result(
            payload(command("andrei_add", amount=500), command("andrei_show"), command("report")),
            VoiceSettings(),
        )
        self.assertEqual([op.is_write for op in operations], [True, False, False])

    def test_invalid_commands_cannot_become_writes(self):
        cases = [
            [command("andrei_show", amount=1)],
            [command("report", category_id="fuel")],
            [command("andrei_add")],
            [command("andrei_add_prompt", amount=500)],
            [command("menu_main"), command("max_add", amount=1)],
            [command("expense_add_prompt", category_id="invented")],
        ]
        for items in cases:
            with self.subTest(items=items), self.assertRaises(ValidationError):
                parse_result(payload(*items), VoiceSettings())

    def test_report_period_is_validated_and_shown(self):
        now = datetime(2026, 11, 25, tzinfo=ZoneInfo("Asia/Bangkok"))
        operations, _ = parse_result(
            payload(command("report", occurred_at="2026-10-01")), VoiceSettings(), now
        )
        self.assertIn("2026-10", operations[0].describe())
        with self.assertRaises(ValidationError):
            parse_result(
                payload(command("andrei_show", occurred_at="2026-10-01")), VoiceSettings(), now
            )

    def test_transaction_executor_refuses_a_read_command(self):
        operations, _ = parse_result(payload(command("max_show")), VoiceSettings())
        with self.assertRaises(ValueError):
            execute_operation(operations[0])


class MenuRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = NS(text="voice", reply_markup=None, reply_text=AsyncMock())
        self.update = NS(effective_message=self.message, callback_query=None)
        self.context = NS(user_data={})

    async def invoke(self, action, **values):
        operations, _ = parse_result(payload(command(action, **values)), VoiceSettings())
        await main.run_voice_command(operations[0], self.update, self.context)

    async def test_voice_budget_reads_use_existing_getters(self):
        for action, getter, menu in (
            ("andrei_show", "andrei_but", "andrei"),
            ("max_show", "max_but", "max"),
        ):
            with (
                self.subTest(action=action),
                patch.object(main, getter, return_value=Decimal("123.45")) as read,
            ):
                await self.invoke(action)
                read.assert_called_once_with()
                self.assertIn("123.45 THB", self.message.reply_text.call_args.args[0])
                self.assertEqual(self.context.user_data["menu"], menu)

    async def test_voice_report_uses_existing_getter_and_requested_month(self):
        period = main.app_now().strftime("%Y-%m")
        with patch.object(main, "get_month_totals", return_value={"Бензин": Decimal("5")}) as read:
            await self.invoke("report", occurred_at=period + "-01")
        read.assert_called_once_with(period)
        self.assertIn(period, self.message.reply_text.call_args.args[0])

    async def test_every_menu_and_prompt_is_routed(self):
        expected_modes = {
            "expense_add_prompt": "add",
            "expense_sub_prompt": "sub",
            "andrei_add_prompt": "add_amb",
            "andrei_sub_prompt": "sub_amb",
            "max_add_prompt": "add_mmb",
            "max_sub_prompt": "sub_mmb",
        }
        for action in NAVIGATION_ACTIONS:
            with self.subTest(action=action):
                await self.invoke(action)
                self.assertEqual(self.context.user_data["mode"], expected_modes.get(action))
                self.assertIsNotNone(self.message.reply_text.call_args.kwargs["reply_markup"])

    async def test_category_prompt_keeps_selected_category(self):
        await self.invoke("expense_sub_prompt", category_id="fuel")
        self.assertEqual(
            self.context.user_data, {"mode": "sub", "category": "Бензин", "menu": "main"}
        )
        self.assertIn("отнять", self.message.reply_text.call_args.args[0].lower())

    async def test_read_after_confirmation_sends_new_message_preserving_receipt(self):
        self.update.callback_query = NS(edit_message_text=AsyncMock())
        with patch.object(main, "andrei_but", return_value=Decimal("20")):
            await self.invoke("andrei_show")
        self.update.callback_query.edit_message_text.assert_not_awaited()
        self.message.reply_text.assert_awaited_once()


class CommandFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(VoiceSettings(), data_dir=Path(self.temp.name))
        self.store = PendingStore(self.settings.data_dir / "voice.sqlite3")
        self.stt = NS(transcribe=AsyncMock(return_value="покажи бюджет Андрея"))
        self.parser = NS(parse=AsyncMock(return_value=payload(command("andrei_show"))))
        self.execute = Mock()
        self.commands = AsyncMock()
        self.controller = VoiceController(
            self.settings,
            self.store,
            self.stt,
            self.parser,
            self.execute,
            {42},
            lambda: None,
            self.commands,
        )
        self.message = NS(
            chat_id=10,
            message_id=1,
            text="preview",
            reply_text=AsyncMock(),
            voice=NS(duration=5, file_size=20, file_id="voice"),
        )
        self.update = NS(
            effective_user=NS(id=42),
            effective_chat=NS(id=10),
            effective_message=self.message,
            callback_query=None,
        )
        file = NS(file_size=20, download_as_bytearray=AsyncMock(return_value=bytearray(b"fake")))
        self.context = NS(bot=NS(id=1, get_file=AsyncMock(return_value=file)), user_data={})

    def latest(self):
        with self.store.connect() as db:
            return dict(db.execute("SELECT * FROM batches ORDER BY rowid DESC LIMIT 1").fetchone())

    async def test_queries_execute_without_confirmation_or_financial_writes(self):
        self.parser.parse.return_value = payload(
            command("andrei_show"), command("max_show"), command("report")
        )
        await self.controller.receive(self.update, self.context)
        self.assertEqual(self.commands.await_count, 3)
        self.execute.assert_not_called()
        self.assertEqual(self.latest()["status"], "completed")
        self.assertFalse(
            any(
                "Подтверждение действует" in call.args[0]
                for call in self.message.reply_text.call_args_list
            )
        )

    async def test_budget_prompt_then_voice_amount_requires_confirmation(self):
        self.controller.command_handler = main.run_voice_command
        self.parser.parse.return_value = payload(command("andrei_add_prompt"))
        await self.controller.receive(self.update, self.context)
        self.assertEqual(self.context.user_data["mode"], "add_amb")
        self.message.message_id = 2
        self.stt.transcribe.return_value = "пятьсот"
        self.parser.parse.return_value = payload(command("andrei_add", amount=500))
        await self.controller.receive(self.update, self.context)
        self.assertEqual(self.parser.parse.call_args.kwargs["dialog"]["mode"], "add_amb")
        self.assertEqual(self.latest()["status"], "pending")
        self.execute.assert_not_called()
        self.update.callback_query = NS(
            data=f"voice:confirm:{self.latest()['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await self.controller.callback(self.update, self.context)
        self.assertEqual(self.execute.call_args.args[0].action, "andrei_add")
        self.assertEqual(self.execute.call_args.args[0].amount, Decimal("500"))

    async def test_mixed_request_reads_only_after_confirmed_writes(self):
        events = []
        self.execute.side_effect = lambda op: events.append(op.action)

        async def read(op, update, context):
            events.append(op.action)

        self.controller.command_handler = read
        self.parser.parse.return_value = payload(
            command("max_add", amount=300), command("max_show"), command("report")
        )
        await self.controller.receive(self.update, self.context)
        self.assertEqual(events, [])
        self.update.callback_query = NS(
            data=f"voice:confirm:{self.latest()['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await self.controller.callback(self.update, self.context)
        self.assertEqual(events, ["max_add", "max_show", "report"])
        await self.controller.callback(self.update, self.context)
        self.execute.assert_called_once()
        receipt = self.update.callback_query.edit_message_text.call_args.args[0]
        self.assertNotIn("✅ Записано: Показать", receipt)

    async def test_failed_write_does_not_run_followup_reads(self):
        self.execute.side_effect = TimeoutError()
        self.parser.parse.return_value = payload(
            command("max_add", amount=300), command("max_show")
        )
        await self.controller.receive(self.update, self.context)
        self.update.callback_query = NS(
            data=f"voice:confirm:{self.latest()['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await self.controller.callback(self.update, self.context)
        self.commands.assert_not_awaited()

    async def test_old_completed_write_receipt_does_not_revalidate_or_repeat_write(self):
        self.parser.parse.return_value = payload(command("andrei_add", amount=500))
        await self.controller.receive(self.update, self.context)
        key = self.latest()["id"]
        self.controller.service.confirm(key, 42, 10)
        with self.store.connect() as db:
            stored = json.loads(self.latest()["operations"])
            stored[0]["occurred_at"] = "2020-01-01"
            db.execute("UPDATE batches SET operations=? WHERE id=?", (json.dumps(stored), key))
        self.message.reply_text.reset_mock()
        self.update.callback_query = NS(
            data=f"voice:confirm:{key}", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await self.controller.callback(self.update, self.context)
        self.execute.assert_called_once()
        self.message.reply_text.assert_not_awaited()
        self.commands.assert_not_awaited()

    async def test_query_failure_never_becomes_a_write(self):
        self.commands.side_effect = TimeoutError()
        await self.controller.receive(self.update, self.context)
        self.execute.assert_not_called()
        self.assertEqual(self.latest()["status"], "failed")

    async def test_read_failure_after_write_preserves_completed_status(self):
        self.commands.side_effect = TimeoutError()
        self.parser.parse.return_value = payload(
            command("max_add", amount=300), command("max_show")
        )
        await self.controller.receive(self.update, self.context)
        self.update.callback_query = NS(
            data=f"voice:confirm:{self.latest()['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await self.controller.callback(self.update, self.context)
        self.assertEqual(self.latest()["status"], "completed")
        self.assertIn("Операции записаны", self.message.reply_text.call_args.args[0])

    async def test_changed_dialog_during_recognition_cancels_stale_command(self):
        async def parse(*args, **kwargs):
            self.context.user_data["menu"] = "max"
            return payload(command("andrei_add", amount=500))

        self.parser.parse.side_effect = parse
        await self.controller.receive(self.update, self.context)
        self.assertEqual(self.latest()["status"], "cancelled")
        self.execute.assert_not_called()

    async def test_parser_receives_only_allowed_dialog_fields(self):
        bridge = HermesBridge(self.settings)
        bridge.call = AsyncMock(
            return_value={"ok": True, "result": payload(command("max_add", amount=5))}
        )
        await bridge.parse(
            "добавь пять", dialog={"mode": "add_mmb", "menu": "max", "secret": "never-send"}
        )
        context = bridge.call.call_args.args[0]["context"]
        self.assertEqual(context["dialog"], {"mode": "add_mmb", "category": None, "menu": "max"})
        self.assertNotIn("never-send", json.dumps(context))
