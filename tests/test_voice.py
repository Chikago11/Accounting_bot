import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from voice.handler import VoiceController
from voice.models import ValidationError, parse_result, validate_operations
from voice.providers import HermesBridge, ProviderError
from voice.service import VoiceService
from voice.settings import VoiceSettings
from voice.store import PendingStore


def item(**changes):
    return (
        dict(
            action="expense_add",
            amount=1000,
            currency="THB",
            category_id="supermarket",
            occurred_at=None,
        )
        | changes
    )


def response(items=None, clarification=None):
    return json.dumps(
        {"operations": items if items is not None else [item()], "clarification": clarification}
    )


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.settings = VoiceSettings()
        self.now = datetime(2026, 11, 25, 0, 30, tzinfo=ZoneInfo("Asia/Bangkok"))

    def test_expenses_and_personal_budgets(self):
        operations, question = parse_result(
            response(
                [
                    item(),
                    item(action="expense_sub", amount=500, category_id="fuel"),
                    item(action="max_add", category_id=None, amount=25.25),
                    item(action="andrei_sub", category_id=None, amount=2),
                ]
            ),
            self.settings,
            self.now,
        )
        self.assertIsNone(question)
        self.assertEqual(len(operations), 4)
        self.assertEqual(operations[2].amount, Decimal("25.25"))
        self.assertEqual(operations[0].occurred_at, "2026-11-25")

    def test_invalid_operations(self):
        invalid = [
            item(amount=0),
            item(amount=-1),
            item(amount=True),
            item(amount="12"),
            item(amount=1e8),
            item(amount=1.001),
            item(amount=float("nan")),
            item(amount=float("inf")),
            item(action="income"),
            item(action=[]),
            item(category_id="invented"),
            item(category_id=[]),
            item(currency="RUB"),
            item(action="max_add"),
            item(occurred_at="tomorrow"),
            item(occurred_at="2026-11-26"),
            item(occurred_at="2020-01-01"),
            item(action="max_sub", category_id=None, occurred_at="2026-11-24"),
            item(unexpected="field"),
            item(occurred_at=7),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_result(response([value]), self.settings, self.now)

    def test_malformed_json_and_batch(self):
        for value in (
            "not json",
            "```json\n{}\n```",
            "[]",
            '{"operations":[],"operations":[],"clarification":null}',
            response([]),
            response([item()] * 11),
            response([item()], "уточните"),
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_result(value, self.settings, self.now)

    def test_clarification_never_contains_operations(self):
        self.assertEqual(
            parse_result(response([], "Какая категория?"), self.settings), ([], "Какая категория?")
        )

    def test_offset_date_is_converted_to_bangkok(self):
        ops = validate_operations(
            [item(occurred_at="2026-11-24T19:30:00+00:00")], self.settings, self.now
        )
        self.assertEqual(ops[0].occurred_at, "2026-11-25")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(VoiceSettings(), data_dir=Path(self.temp.name))
        self.store = PendingStore(self.settings.data_dir / "test.sqlite3")
        self.calls = []
        self.service = VoiceService(self.store, self.settings, self.calls.append)

    def batch(self, operations=None):
        row, _ = self.store.reserve("bot:chat:message:user", 42, 10)
        ops, _ = parse_result(response(operations), self.settings)
        self.store.pending(row["id"], "synthetic transcript", ops)
        return row["id"]

    def test_duplicate_messages_are_reserved_once(self):
        first, fresh = self.store.reserve("same", 42, 10)
        second, again = self.store.reserve("same", 42, 10)
        self.assertTrue(fresh)
        self.assertFalse(again)
        self.assertEqual(first["id"], second["id"])

    def test_confirmation_once_and_survives_restart(self):
        key = self.batch([item(), item(category_id="fuel", amount=500)])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.confirm(key, 42, 10)["status"], "completed")
        restarted = PendingStore(self.store.path)
        restarted.recover()
        VoiceService(restarted, self.settings, self.calls.append).confirm(key, 42, 10)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(restarted.attempts(key), {0: "completed", 1: "completed"})

    def test_wrong_user_and_chat(self):
        key = self.batch()
        self.assertIsNone(self.service.confirm(key, 43, 10))
        self.assertIsNone(self.service.confirm(key, 42, 11))
        self.assertFalse(self.store.cancel(key, 43, 10))
        self.assertEqual(self.calls, [])

    def test_cancelled_and_expired_never_write(self):
        key = self.batch()
        self.assertTrue(self.store.cancel(key, 42, 10))
        self.service.confirm(key, 42, 10)
        with self.store.connect() as db:
            db.execute(
                "UPDATE batches SET status='pending',expires=? WHERE id=?", (time.time() - 1, key)
            )
        self.assertEqual(self.service.confirm(key, 42, 10)["status"], "expired")
        self.assertEqual(self.calls, [])

    def test_partial_failure_is_never_retried(self):
        key = self.batch([item(), item(amount=2), item(amount=3)])

        def write(op):
            self.calls.append(op)
            if len(self.calls) == 2:
                raise TimeoutError()

        service = VoiceService(self.store, self.settings, write)
        self.assertEqual(service.confirm(key, 42, 10)["status"], "failed")
        service.confirm(key, 42, 10)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.store.attempts(key), {0: "completed", 1: "uncertain"})

    def test_interrupted_write_is_not_replayed(self):
        key = self.batch()
        self.assertTrue(self.store.claim(key, 42, 10))
        self.store.mark_attempt(key, 0, "executing")
        self.store.recover()
        self.assertEqual(self.service.confirm(key, 42, 10)["status"], "failed")
        self.assertEqual(self.store.attempts(key), {0: "uncertain"})
        self.assertEqual(self.calls, [])

    def test_competing_confirmations_have_one_writer(self):
        key = self.batch()
        entered, release = threading.Event(), threading.Event()

        def write(op):
            self.calls.append(op)
            entered.set()
            release.wait(5)

        service = VoiceService(self.store, self.settings, write)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(service.confirm, key, 42, 10)
            self.assertTrue(entered.wait(3))
            second = pool.submit(service.confirm, key, 42, 10)
            self.assertEqual(second.result(3)["status"], "processing")
            self.assertFalse(self.store.cancel(key, 42, 10))
            release.set()
            self.assertEqual(first.result(3)["status"], "completed")
        self.assertEqual(len(self.calls), 1)

    def test_entire_batch_revalidated_before_write(self):
        key = self.batch()
        with self.store.connect() as db:
            db.execute(
                "UPDATE batches SET operations=? WHERE id=?",
                (json.dumps([item(), item(category_id="bad")]), key),
            )
        self.assertEqual(self.service.confirm(key, 42, 10)["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_failure_to_journal_success_never_replays_write(self):
        key = self.batch()
        original = self.store.mark_attempt

        def mark(batch, position, status):
            if status == "completed":
                raise OSError("synthetic disk failure")
            original(batch, position, status)

        self.store.mark_attempt = mark
        self.assertEqual(self.service.confirm(key, 42, 10)["status"], "failed")
        self.service.confirm(key, 42, 10)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.attempts(key), {0: "uncertain"})

    def test_pending_confirmation_survives_restart(self):
        key = self.batch()
        restarted = PendingStore(self.store.path)
        restarted.recover()
        service = VoiceService(restarted, self.settings, self.calls.append)
        self.assertEqual(service.confirm(key, 42, 10)["status"], "completed")
        self.assertEqual(len(self.calls), 1)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(VoiceSettings(), data_dir=Path(self.temp.name))
        self.store = PendingStore(self.settings.data_dir / "voice.sqlite3")
        self.stt = NS(transcribe=AsyncMock(return_value="Магазин тысяча бат"))
        self.parser = NS(parse=AsyncMock(return_value=response()))
        self.execute = Mock()
        self.controller = VoiceController(
            self.settings, self.store, self.stt, self.parser, self.execute, {42}, lambda: None
        )
        self.message = NS(
            chat_id=10,
            message_id=7,
            text="preview",
            reply_text=AsyncMock(),
            voice=NS(duration=10, file_size=500, file_id="file"),
        )
        self.update = NS(
            effective_user=NS(id=42), effective_chat=NS(id=10), effective_message=self.message
        )
        file = NS(
            file_size=500, download_as_bytearray=AsyncMock(return_value=bytearray(b"fake ogg"))
        )
        self.context = NS(bot=NS(id=100, get_file=AsyncMock(return_value=file)), user_data={})

    def key(self):
        with self.store.connect() as db:
            return db.execute("SELECT id FROM batches").fetchone()[0]

    async def test_voice_then_confirmation(self):
        await self.controller.receive(self.update, self.context)
        self.execute.assert_not_called()
        key = self.key()
        self.assertEqual(self.store.get(key, 42, 10)["status"], "pending")
        self.update.callback_query = NS(
            data=f"voice:confirm:{key}", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await self.controller.callback(self.update, self.context)
        await self.controller.callback(self.update, self.context)
        self.execute.assert_called_once()

    async def test_edit_cancels_without_writing(self):
        await self.controller.receive(self.update, self.context)
        key = self.key()
        self.update.callback_query = NS(
            data=f"voice:edit:{key}", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await self.controller.callback(self.update, self.context)
        self.assertEqual(self.store.get(key, 42, 10)["status"], "cancelled")
        self.execute.assert_not_called()

    async def test_duplicate_delivery_does_not_reparse(self):
        await self.controller.receive(self.update, self.context)
        await self.controller.receive(self.update, self.context)
        self.stt.transcribe.assert_awaited_once()
        self.execute.assert_not_called()

    async def test_stt_failure_does_not_write(self):
        self.stt.transcribe.side_effect = ProviderError("offline")
        await self.controller.receive(self.update, self.context)
        self.parser.parse.assert_not_awaited()
        self.execute.assert_not_called()
        self.assertEqual(self.store.get(self.key(), 42, 10)["status"], "failed")

    async def test_parser_failure_does_not_write(self):
        self.parser.parse.side_effect = asyncio.TimeoutError()
        await self.controller.receive(self.update, self.context)
        self.execute.assert_not_called()
        self.assertEqual(self.store.get(self.key(), 42, 10)["status"], "failed")

    async def test_bad_json_does_not_write(self):
        self.parser.parse.return_value = "not JSON"
        await self.controller.receive(self.update, self.context)
        self.execute.assert_not_called()

    async def test_clarification_does_not_create_confirmable_batch(self):
        self.parser.parse.return_value = response([], "Какая сумма?")
        await self.controller.receive(self.update, self.context)
        self.assertEqual(self.store.get(self.key(), 42, 10)["status"], "cancelled")
        self.execute.assert_not_called()

    async def test_unknown_user_and_large_voice_are_rejected(self):
        self.update.effective_user.id = 99
        await self.controller.receive(self.update, self.context)
        self.update.effective_user.id = 42
        self.message.voice.duration = 121
        await self.controller.receive(self.update, self.context)
        self.message.voice.duration = 10
        self.message.voice.file_size = 10_000_001
        await self.controller.receive(self.update, self.context)
        self.context.bot.get_file.assert_not_awaited()

    async def test_synonyms_and_current_categories_sent_to_parser(self):
        bridge = HermesBridge(self.settings)
        bridge.call = AsyncMock(return_value={"ok": True, "result": response()})
        await bridge.parse("Заправка 500")
        context = bridge.call.call_args.args[0]["context"]
        self.assertIn("заправка", context["synonyms"]["fuel"])
        self.assertEqual(context["categories"]["fuel"], "Бензин")
        self.assertNotIn("TELEGRAM_TOKEN", json.dumps(context))
