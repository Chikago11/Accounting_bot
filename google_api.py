import gspread
from datetime import datetime
from decimal import Decimal
from functools import wraps
import fcntl
import threading
from config import DATA_DIR, GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_NAME

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


def andrei_but():
    gc = client()
    sh = gc.open(SPREADSHEET_NAME)
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(1, 2).value
    return decimal_value(value)


@serialized_write
def andrei_mb(act: str, amount: float):
    gc = client()
    sh = gc.open(SPREADSHEET_NAME)
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(1, 2).value
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
    gc = client()
    sh = gc.open(SPREADSHEET_NAME)
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(2, 2).value
    return decimal_value(value)


@serialized_write
def max_mb(act: str, amount: float):
    gc = client()
    sh = gc.open(SPREADSHEET_NAME)
    ls = sh.worksheet("andrei_mb")
    value = ls.cell(2, 2).value
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
    gc = client()
    sh = gc.open(SPREADSHEET_NAME)
    return sh.sheet1


def get_month_col(sheet, month_key: str) -> int:
    # month_key, например "2025-12"
    header = sheet.row_values(1)  # заголовки первой строки

    # пробуем найти такой столбец
    for idx, value in enumerate(header, start=1):
        if value == month_key:
            return idx

    # если не нашли — добавляем новый столбец в конец
    new_col_index = len(header) + 1
    sheet.update_cell(1, new_col_index, month_key)
    return new_col_index


def get_category_row(sheet, category: str) -> int:
    column = sheet.col_values(1)  # весь столбец A

    for idx, value in enumerate(column, start=1):
        if value == category:
            return idx

    # если категории нет — добавляем новую строку в конец
    new_row_index = len(column) + 1
    sheet.update_cell(new_row_index, 1, category)
    return new_row_index


@serialized_write
def add_expense_matrix(category: str, amount: float, dt: datetime | None = None):
    if dt is None:
        dt = datetime.now()

    month_key = dt.strftime("%Y-%m")  # например "2025-12"

    sheet = get_sheet()

    row = get_category_row(sheet, category)
    col = get_month_col(sheet, month_key)

    # читаем текущее значение ячейки
    value = sheet.cell(row, col).value
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
    col_values = sheet.col_values(month_col)

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
