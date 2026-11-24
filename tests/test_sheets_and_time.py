from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gspread.utils import ValueRenderOption
import app_time
import google_api
import main
from voice.settings import VoiceSettings


class SpreadsheetSelectionTests(unittest.TestCase):
    def test_id_takes_priority_over_name(self):
        gc = Mock()
        with (
            patch.object(google_api, "client", return_value=gc),
            patch.object(google_api, "SPREADSHEET_ID", "test-id"),
        ):
            result = google_api.open_spreadsheet()
        gc.open_by_key.assert_called_once_with("test-id")
        gc.open.assert_not_called()
        self.assertIs(result, gc.open_by_key.return_value)

    def test_legacy_name_is_supported(self):
        gc = Mock()
        with (
            patch.object(google_api, "client", return_value=gc),
            patch.object(google_api, "SPREADSHEET_ID", ""),
        ):
            google_api.open_spreadsheet()
        gc.open.assert_called_once_with(google_api.SPREADSHEET_NAME)
        gc.open_by_key.assert_not_called()

    def test_failed_id_never_falls_back_to_different_sheet(self):
        gc = Mock()
        gc.open_by_key.side_effect = PermissionError()
        with (
            patch.object(google_api, "client", return_value=gc),
            patch.object(google_api, "SPREADSHEET_ID", "test-id"),
        ):
            with self.assertRaises(PermissionError):
                google_api.open_spreadsheet()
        gc.open.assert_not_called()


class NumericReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(google_api, "DATA_DIR", Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.sheet = Mock()
        # A localized display value is deliberately not a valid Decimal string.
        self.sheet.cell.side_effect = lambda row, col, value_render_option=None: NS(
            value=1234.5 if value_render_option == ValueRenderOption.unformatted else "1 234,50 ฿"
        )

    def test_budget_reads_and_writes_use_numeric_values(self):
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = self.sheet
        with patch.object(google_api, "open_spreadsheet", return_value=spreadsheet):
            self.assertEqual(google_api.andrei_but(), Decimal("1234.5"))
            self.assertEqual(google_api.max_but(), Decimal("1234.5"))
            google_api.andrei_mb("add_amb", Decimal("0.25"))
            self.sheet.update_cell.assert_called_with(1, 2, 1234.75)
            google_api.max_mb("sub_mmb", Decimal("0.25"))
            self.sheet.update_cell.assert_called_with(2, 2, 1234.25)

    def test_expense_uses_numeric_value_and_keeps_formatted_headers(self):
        self.sheet.row_values.return_value = ["Категория", "2026-11"]
        self.sheet.col_values.return_value = ["Категория", "Бензин", "Итого"]
        with patch.object(google_api, "get_sheet", return_value=self.sheet):
            google_api.add_expense_matrix("Бензин", Decimal("0.25"), datetime(2026, 11, 25))
        self.sheet.update_cell.assert_called_once_with(2, 2, 1234.75)
        self.sheet.row_values.assert_called_once_with(1)

    def test_report_reads_numeric_amounts_and_existing_total_row(self):
        self.sheet.row_values.return_value = ["Категория", "2026-11"]

        def values(col, value_render_option=None):
            if col == 1:
                return ["Категория", "Бензин", "Разное", "Итого"]
            if value_render_option == ValueRenderOption.unformatted:
                return [46327, 1234.5, 0, 1234.5]
            return ["2026-11", "1 234,50 ฿", "0,00 ฿", "1 234,50 ฿"]

        self.sheet.col_values.side_effect = values
        with patch.object(google_api, "get_sheet", return_value=self.sheet):
            result = google_api.get_month_totals("2026-11")
        self.assertEqual(
            result,
            {"Бензин": Decimal("1234.5"), "Разное": Decimal("0"), "Итого": Decimal("1234.5")},
        )
        self.sheet.update_cell.assert_not_called()


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 11, 30, 18, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)


class TimezoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_month_boundary_is_shared_by_buttons_report_and_voice(self):
        with (
            patch.dict(
                os.environ, {"TZ": "UTC", "APP_TIMEZONE": "Asia/Bangkok", "VOICE_TIMEZONE": "UTC"}
            ),
            patch.object(app_time, "datetime", FrozenDateTime),
        ):
            now = app_time.now()
            self.assertEqual(now.isoformat(), "2026-12-01T01:30:00+07:00")
            self.assertEqual(VoiceSettings.from_env().timezone, "Asia/Bangkok")
            message = NS(reply_text=AsyncMock())
            update = NS(effective_message=message, callback_query=None)
            context = NS(user_data={})
            with patch.object(main, "get_month_totals", return_value={}) as totals:
                await main.report(update, context)
            totals.assert_called_once_with("2026-12")
            sheet = Mock()
            sheet.cell.return_value.value = 0
            with (
                tempfile.TemporaryDirectory() as temp,
                patch.object(google_api, "DATA_DIR", Path(temp)),
                patch.object(google_api, "get_sheet", return_value=sheet),
                patch.object(google_api, "get_category_row", return_value=2),
                patch.object(google_api, "get_month_col", return_value=3) as month,
            ):
                google_api.add_expense_matrix("Бензин", Decimal("1"))
            month.assert_called_once_with(sheet, "2026-12")

    async def test_legacy_timezone_and_default_are_preserved(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app_time.timezone_name(), "Asia/Bangkok")
            os.environ["VOICE_TIMEZONE"] = "Europe/Moscow"
            self.assertEqual(app_time.timezone_name(), "Europe/Moscow")
            self.assertEqual(VoiceSettings().timezone, "Europe/Moscow")
