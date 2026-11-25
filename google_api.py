import gspread
from datetime import datetime
from decimal import Decimal
from functools import wraps
import fcntl
import threading
from gspread.utils import ValueRenderOption, rowcol_to_a1
from app_time import now as app_now
from config import DATA_DIR, GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, SPREADSHEET_NAME
from errors import SheetLayoutError

_write_lock = threading.RLock()


def serialized_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _write_lock, (DATA_DIR / "sheets-write.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    return wrapped


def decimal_value(value):
    amount = Decimal(str(value)) if value not in (None, "") else Decimal("0")
    if not amount.is_finite():
        raise ValueError("Invalid money value")
    return amount


def client():
    gc = gspread.service_account(GOOGLE_SERVICE_ACCOUNT_FILE)
    gc.set_timeout(20)
    return gc


def open_spreadsheet():
    gc = client()
    if SPREADSHEET_ID:
        return gc.open_by_key(SPREADSHEET_ID)
    return gc.open(SPREADSHEET_NAME)


def andrei_but():
    sh = open_spreadsheet()
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(1, 2, value_render_option=ValueRenderOption.unformatted).value
    return decimal_value(value)


@serialized_write
def andrei_mb(act: str, amount: float):
    sh = open_spreadsheet()
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(1, 2, value_render_option=ValueRenderOption.unformatted).value
    current = decimal_value(value)
    amount = decimal_value(amount)
    if act == "add_amb":
        new_value = current + amount
    elif act == "sub_amb":
        new_value = current - amount
    else:
        return False
    ls.update_cell(1, 2, float(new_value))
    return True


def max_but():
    sh = open_spreadsheet()
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(2, 2, value_render_option=ValueRenderOption.unformatted).value
    return decimal_value(value)


@serialized_write
def max_mb(act: str, amount: float):
    sh = open_spreadsheet()
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(2, 2, value_render_option=ValueRenderOption.unformatted).value
    current = decimal_value(value)
    amount = decimal_value(amount)
    if act == "add_mmb":
        new_value = current + amount
    elif act == "sub_mmb":
        new_value = current - amount
    else:
        return False
    ls.update_cell(2, 2, float(new_value))
    return True


def get_sheet():
    sh = open_spreadsheet()
    return sh.sheet1


def total_row(labels):
    matches = [
        i
        for i, label in enumerate(labels, 1)
        if isinstance(label, str) and label.strip().casefold() == "итого"
    ]
    if len(matches) > 1 or (matches and matches[0] < 3):
        raise SheetLayoutError("В таблице должна быть одна строка «Итого» после категорий.")
    return matches[0] if matches else None


def get_month_col(sheet, month_key: str) -> int:
    # month_key, например "2025-12"
    header = sheet.row_values(1)  # заголовки первой строки

    # пробуем найти такой столбец
    for idx, value in enumerate(header, start=1):
        if value == month_key:
            return idx

    # Create the header and total in one batch, keeping existing months unchanged.
    if not header:
        raise SheetLayoutError(
            "В первой строке таблицы нет заголовков. Проверьте её перед добавлением месяца."
        )
    end_row = total_row(sheet.col_values(1))
    if end_row is None:
        raise SheetLayoutError("В таблице нет строки «Итого». Добавьте её после категорий.")
    new_col_index = len(header) + 1
    existing = (
        sheet.col_values(new_col_index, value_render_option=ValueRenderOption.formula)
        if new_col_index <= sheet.col_count
        else []
    )
    if any(value not in (None, "") for i, value in enumerate(existing, 1) if i != end_row):
        raise SheetLayoutError(
            "Столбец для нового месяца уже содержит данные. Проверьте заголовки таблицы."
        )
    previous_total = existing[end_row - 1] if len(existing) >= end_row else None
    if previous_total not in (None, "") and not (
        isinstance(previous_total, str) and previous_total.startswith("=")
    ):
        raise SheetLayoutError(
            "В «Итого» нового месяца уже записано значение. Проверьте эту ячейку."
        )

    cells = [(1, {"stringValue": month_key})]
    if previous_total in (None, ""):
        start = rowcol_to_a1(2, new_col_index)
        end = rowcol_to_a1(end_row - 1, new_col_index)
        cells.append((end_row, {"formulaValue": f"=SUM({start}:{end})"}))
    requests = [
        {
            "updateCells": {
                "start": {
                    "sheetId": sheet.id,
                    "rowIndex": row - 1,
                    "columnIndex": new_col_index - 1,
                },
                "rows": [{"values": [{"userEnteredValue": value}]}],
                "fields": "userEnteredValue",
            }
        }
        for row, value in cells
    ]
    if new_col_index > sheet.col_count:
        requests.insert(
            0,
            {
                "appendDimension": {
                    "sheetId": sheet.id,
                    "dimension": "COLUMNS",
                    "length": new_col_index - sheet.col_count,
                }
            },
        )
    sheet.spreadsheet.batch_update({"requests": requests})
    return new_col_index


def get_category_row(sheet, category: str) -> int:
    column = sheet.col_values(1)  # весь столбец A
    rows = [idx for idx, value in enumerate(column, 1) if value == category]
    if not rows:
        raise SheetLayoutError(
            "Категория отсутствует в таблице. Добавьте её перед строкой «Итого» и проверьте формулы суммы."
        )
    if len(rows) != 1:
        raise SheetLayoutError("Категория повторяется в таблице. Оставьте для неё одну строку.")
    end_row = total_row(column)
    if rows[0] < 2 or (end_row is not None and rows[0] >= end_row):
        raise SheetLayoutError("Категория должна находиться между заголовком и строкой «Итого».")
    return rows[0]


@serialized_write
def add_expense_matrix(category: str, amount: float, dt: datetime | None = None):
    if dt is None:
        dt = app_now()

    month_key = dt.strftime("%Y-%m")  # например "2025-12"

    sheet = get_sheet()

    row = get_category_row(sheet, category)
    col = get_month_col(sheet, month_key)

    # читаем текущее значение ячейки
    value = sheet.cell(row, col, value_render_option=ValueRenderOption.unformatted).value
    current = decimal_value(value)

    new_value = current + decimal_value(amount)

    # Sheets accepts JSON numbers; all arithmetic before this boundary is Decimal.
    sheet.update_cell(row, col, float(new_value))


# ------------------------------------------------------
# Получаем отчёт ПО МЕСЯЦУ
# month_key формата "2025-12"
# Возвращает словарь: {"Еда": 1200.0, "Кафе": 300.0, ...}
# ------------------------------------------------------


def get_month_totals(month_key: str):
    sheet = get_sheet()

    header = sheet.row_values(1)

    # Ищем столбец месяца
    try:
        month_col = header.index(month_key) + 1  # +1, т.к. index() даёт 0-based
    except ValueError:
        return {}  # месяца нет в таблице — пустой отчёт

    categories = sheet.col_values(1)  # столбец A

    totals = {}
    # Читаем значения столбца
    col_values = sheet.col_values(month_col, value_render_option=ValueRenderOption.unformatted)

    # Пропускаем первую строку (заголовки)
    for i in range(1, min(len(col_values), len(categories))):
        category = categories[i]
        value = col_values[i]

        try:
            amount = decimal_value(value)
        except (ValueError, ArithmeticError):
            amount = Decimal("0")

        totals[category] = amount

    return totals
