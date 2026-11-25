from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main
import google_api
from errors import SheetLayoutError
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

    async def test_nonpositive_amounts_rejected_in_all_button_modes(self):
        with (
            patch.object(main, "add_expense_matrix") as expense,
            patch.object(main, "andrei_mb") as andrei,
            patch.object(main, "max_mb") as max_budget,
        ):
            for mode in ("add", "sub", "add_amb", "sub_amb", "add_mmb", "sub_mmb"):
                for text in ("-100", "0", "-0", "0.00"):
                    with self.subTest(mode=mode, text=text):
                        self.context.user_data.update(mode=mode, category="Бензин")
                        self.message.text = text
                        await main.add_amount(self.update, self.context)
                        self.assertIn("положительную", self.message.reply_text.call_args.args[0])
            expense.assert_not_called()
            andrei.assert_not_called()
            max_budget.assert_not_called()

    async def test_positive_decimal_with_comma_still_works(self):
        self.message.text = "0,25"
        with patch.object(main, "add_expense_matrix") as write:
            await main.add_amount(self.update, self.context)
        write.assert_called_once_with("Супермаркет", Decimal("0.25"))

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

    async def test_new_keyboards_use_short_ids(self):
        for handler, mode in ((main.add_command, "add"), (main.sub_command, "sub")):
            await handler(self.update, self.context)
            keyboard = self.message.reply_text.call_args.kwargs["reply_markup"]
            for row, (key, label) in zip(keyboard.inline_keyboard, main.CATEGORY_LABELS.items()):
                self.assertEqual(row[0].text, label)
                self.assertEqual(row[0].callback_data, f"expense:{mode}:{key}")
                self.assertLessEqual(len(row[0].callback_data.encode()), 64)

    async def test_new_and_old_category_callbacks(self):
        for data, mode in (
            ("expense:add:fuel", "add"),
            ("expense:sub:fuel", "sub"),
            ("expense:add:Бензин", "add"),
            ("expense:sub:Бензин", "sub"),
        ):
            with self.subTest(data=data):
                self.update.callback_query = NS(
                    data=data, answer=AsyncMock(), edit_message_text=AsyncMock()
                )
                self.message.reply_markup = None
                self.context.user_data.update(mode=None, category=None)
                await main.category_chosen(self.update, self.context)
                self.assertEqual(
                    self.context.user_data, {"mode": mode, "category": "Бензин", "menu": "main"}
                )

    async def test_unknown_callback_category_is_rejected(self):
        self.update.callback_query = NS(
            data="expense:add:invented", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        self.context.user_data.update(mode=None, category=None)
        await main.category_chosen(self.update, self.context)
        self.assertIsNone(self.context.user_data["category"])
        self.update.callback_query.edit_message_text.assert_not_awaited()

    async def test_error_log_has_location_without_sensitive_content(self):
        secret = "synthetic-secret-token and private financial text"

        def fail():
            raise RuntimeError(secret)

        try:
            fail()
        except RuntimeError as error:
            self.context.error = error
        with self.assertLogs("main", level="ERROR") as logs:
            await main.on_error(self.update, self.context)
        text = "\n".join(logs.output)
        self.assertIn("code=RuntimeError", text)
        self.assertIn("test_buttons.py:", text)
        self.assertIn(":fail", text)
        self.assertNotIn(secret, text)
        self.assertNotIn("raise RuntimeError(secret)", text)

    async def test_failed_error_notification_does_not_escape_handler(self):
        self.context.error = ValueError("private first error")
        self.message.reply_text.side_effect = RuntimeError("private notification error")
        with self.assertLogs("main", level="ERROR") as logs:
            await main.on_error(self.update, self.context)
        text = "\n".join(logs.output)
        self.assertIn("error_reply_failed code=RuntimeError", text)
        self.assertNotIn("private", text)

    async def test_missing_category_has_actionable_error(self):
        self.context.error = SheetLayoutError(
            "Категория отсутствует в таблице. Добавьте её перед «Итого»."
        )
        with self.assertLogs("main", level="ERROR"):
            await main.on_error(self.update, self.context)
        text = self.message.reply_text.call_args.args[0]
        self.assertIn("Операция не записана", text)
        self.assertIn("перед «Итого»", text)

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
