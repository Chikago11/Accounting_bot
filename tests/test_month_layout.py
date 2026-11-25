from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gspread.utils import ValueRenderOption
import google_api
from errors import SheetLayoutError


class MonthLayoutTests(unittest.TestCase):
    def setUp(self):
        self.sheet = Mock(id=7, col_count=26)
        self.header = ["Категория", "2026-11"]
        self.labels = ["Категория", "Бензин", "Супермаркет", "Итого"]
        self.new_column = []
        self.sheet.row_values.side_effect = lambda row: list(self.header)
        self.sheet.col_values.side_effect = lambda col, **kwargs: list(
            self.labels if col == 1 else self.new_column
        )

    def updates(self):
        requests = self.sheet.spreadsheet.batch_update.call_args.args[0]["requests"]
        return {
            (
                item["updateCells"]["start"]["rowIndex"] + 1,
                item["updateCells"]["start"]["columnIndex"] + 1,
            ): item["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
            for item in requests
            if "updateCells" in item
        }

    def test_new_month_header_and_sum_share_one_request(self):
        self.assertEqual(google_api.get_month_col(self.sheet, "2026-12"), 3)
        self.sheet.spreadsheet.batch_update.assert_called_once()
        self.assertEqual(
            self.updates(),
            {
                (1, 3): {"stringValue": "2026-12"},
                (4, 3): {"formulaValue": "=SUM(C2:C3)"},
            },
        )
        self.sheet.update_cell.assert_not_called()

    def test_total_position_and_column_letters_are_dynamic(self):
        self.header = ["Категория"] + [f"old-{i}" for i in range(26)]
        self.labels = ["Категория", "A", "B", "C", "D", "  ИТОГО  "]
        self.sheet.col_count = 27
        self.assertEqual(google_api.get_month_col(self.sheet, "2026-12"), 28)
        self.assertEqual(self.updates()[(6, 28)], {"formulaValue": "=SUM(AB2:AB5)"})
        requests = self.sheet.spreadsheet.batch_update.call_args.args[0]["requests"]
        self.assertEqual(
            requests[0], {"appendDimension": {"sheetId": 7, "dimension": "COLUMNS", "length": 1}}
        )

    def test_existing_month_is_not_modified(self):
        self.assertEqual(google_api.get_month_col(self.sheet, "2026-11"), 2)
        self.sheet.spreadsheet.batch_update.assert_not_called()
        self.sheet.col_values.assert_not_called()

    def test_prepared_total_formula_is_preserved(self):
        self.new_column = ["", "", "", "=SUM(C2:C3)"]
        google_api.get_month_col(self.sheet, "2026-12")
        self.assertEqual(self.updates(), {(1, 3): {"stringValue": "2026-12"}})

    def test_new_month_never_overwrites_existing_data(self):
        for values in (["", 123], ["", "", "", 0], ["", "", "", "manual total"]):
            with self.subTest(values=values):
                self.new_column = values
                with self.assertRaises(SheetLayoutError):
                    google_api.get_month_col(self.sheet, "2026-12")
        self.sheet.spreadsheet.batch_update.assert_not_called()

    def test_invalid_total_layout_prevents_creation(self):
        for labels in (
            ["Категория", "Бензин"],
            ["Категория", "Итого"],
            ["Категория", "Бензин", "Итого", "Итого"],
        ):
            with self.subTest(labels=labels):
                self.labels = labels
                with self.assertRaises(SheetLayoutError):
                    google_api.get_month_col(self.sheet, "2026-12")
        self.sheet.spreadsheet.batch_update.assert_not_called()

    def test_empty_header_never_overwrites_category_column(self):
        self.header = []
        with self.assertRaises(SheetLayoutError):
            google_api.get_month_col(self.sheet, "2026-12")
        self.sheet.spreadsheet.batch_update.assert_not_called()

    def test_missing_or_misplaced_category_is_not_inserted(self):
        for labels in (
            ["Категория", "Супермаркет", "Итого"],
            ["Категория", "Супермаркет", "Итого", "Бензин"],
            ["Категория", "Бензин", "Бензин", "Итого"],
        ):
            with self.subTest(labels=labels):
                self.labels = labels
                with self.assertRaises(SheetLayoutError):
                    google_api.get_category_row(self.sheet, "Бензин")
        self.sheet.update_cell.assert_not_called()
        self.sheet.spreadsheet.batch_update.assert_not_called()

    def test_missing_category_blocks_month_creation_and_amount_write(self):
        self.labels = ["Категория", "Супермаркет", "Итого"]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(google_api, "DATA_DIR", Path(temp)),
            patch.object(google_api, "get_sheet", return_value=self.sheet),
        ):
            with self.assertRaises(SheetLayoutError):
                google_api.add_expense_matrix("Бензин", Decimal("1"), datetime(2026, 12, 1))
        self.sheet.update_cell.assert_not_called()
        self.sheet.spreadsheet.batch_update.assert_not_called()

    def test_failed_month_creation_cannot_write_expense(self):
        self.sheet.spreadsheet.batch_update.side_effect = TimeoutError()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(google_api, "DATA_DIR", Path(temp)),
            patch.object(google_api, "get_sheet", return_value=self.sheet),
        ):
            with self.assertRaises(TimeoutError):
                google_api.add_expense_matrix("Бензин", Decimal("1"), datetime(2026, 12, 1))
        self.sheet.update_cell.assert_not_called()

    def test_formula_expense_cell_is_replaced_with_updated_number(self):
        # A formula such as =100+200 is read as 300 through UNFORMATTED_VALUE.
        self.sheet.cell.return_value.value = 300
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(google_api, "DATA_DIR", Path(temp)),
            patch.object(google_api, "get_sheet", return_value=self.sheet),
        ):
            google_api.add_expense_matrix("Бензин", Decimal("50"), datetime(2026, 11, 25))
        self.sheet.cell.assert_called_once_with(
            2, 2, value_render_option=ValueRenderOption.unformatted
        )
        self.sheet.update_cell.assert_called_once_with(2, 2, 350.0)
