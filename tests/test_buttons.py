from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main
import google_api
from transactions import execute_operation
from voice.models import Operation


class ButtonsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = NS(text="250.25", reply_text=AsyncMock())
        self.update = NS(message=self.message, effective_message=self.message, callback_query=None)
        self.context = NS(user_data={"mode": "add", "category": "Супермаркет"}, bot_data={})

    async def test_expense_add_and_sub(self):
        for mode, expected in (("add", Decimal("250.25")), ("sub", Decimal("-250.25"))):
            with self.subTest(mode=mode), patch.object(main, "add_expense_matrix") as write:
                self.context.user_data.update(mode=mode, category="Супермаркет")
                await main.add_amount(self.update, self.context)
                write.assert_called_once_with("Супермаркет", expected)
                self.assertIsNone(self.context.user_data["mode"])

    async def test_both_personal_budgets_add_and_sub(self):
        for mode, fn in (
            ("add_amb", "andrei_mb"),
            ("sub_amb", "andrei_mb"),
            ("add_mmb", "max_mb"),
            ("sub_mmb", "max_mb"),
        ):
            with self.subTest(mode=mode), patch.object(main, fn, return_value=True) as write:
                self.context.user_data.update(mode=mode, category=None)
                await main.add_amount(self.update, self.context)
                write.assert_called_once_with(mode, Decimal("250.25"))

    async def test_invalid_money_cannot_write(self):
        with patch.object(main, "add_expense_matrix") as write:
            for text in ("NaN", "Infinity", "-Infinity", "1e100", "1.001", "hello"):
                self.message.text = text
                await main.add_amount(self.update, self.context)
            write.assert_not_called()

    async def test_report_reads_existing_totals(self):
        with patch.object(main, "get_month_totals", return_value={"Бензин": Decimal("500.50")}):
            await main.report(self.update, self.context)
        self.assertIn("Бензин: 500.50", self.message.reply_text.call_args.args[0])

    async def test_categories_and_old_callback(self):
        await main.add_command(self.update, self.context)
        keyboard = self.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(len(keyboard.inline_keyboard), len(main.CATEGORIES) + 1)
        self.update.callback_query = NS(
            data="Бензин", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        self.message.reply_markup = None
        await main.category_chosen(self.update, self.context)
        self.assertEqual(self.context.user_data["category"], "Бензин")

    async def test_voice_disabled_does_not_require_configuration(self):
        with patch.dict(
            "os.environ", {"VOICE_INPUT_ENABLED": "false", "VOICE_BRIDGE_COMMAND": "invalid"}
        ):
            app = main.build_application()
        self.assertNotIn("voice", app.bot_data)
        await app.bot.shutdown()


class SharedWritesTests(unittest.TestCase):
    def test_voice_uses_existing_functions(self):
        with patch.object(google_api, "add_expense_matrix") as write:
            execute_operation(
                Operation("expense_sub", Decimal("2.50"), "THB", "fuel", "2026-11-24")
            )
            self.assertEqual(write.call_args.args[:2], ("Бензин", Decimal("-2.50")))
        for action, function, legacy in (
            ("max_add", "max_mb", "add_mmb"),
            ("andrei_sub", "andrei_mb", "sub_amb"),
        ):
            with patch.object(google_api, function, return_value=True) as write:
                execute_operation(Operation(action, Decimal("10"), "THB", None, "2026-11-24"))
                write.assert_called_once_with(legacy, Decimal("10"))

    def test_decimal_arithmetic_and_no_real_sheets(self):
        sheet = Mock()
        sheet.cell.return_value.value = "0.1"
        sheet.row_values.return_value = ["Категория", "2026-11"]
        sheet.col_values.return_value = ["Категория", "Бензин"]
        from datetime import datetime

        with (
            tempfile.TemporaryDirectory() as path,
            patch.object(google_api, "DATA_DIR", Path(path)),
            patch.object(google_api, "get_sheet", return_value=sheet),
        ):
            google_api.add_expense_matrix("Бензин", Decimal("0.2"), datetime(2026, 11, 24))
        sheet.update_cell.assert_called_once_with(2, 2, 0.3)
