"""The category list shared by buttons, voice parsing and validation."""

CATEGORY_LABELS = {
    "home": "Дом, телефон, интернет",
    "supermarket": "Супермаркет",
    "max_school": "Школа Макс",
    "max_activities": "Секции Макс",
    "insurance": "Страховка",
    "visa": "Виза",
    "medicine": "Медицина",
    "vehicle": "Машина/Байк",
    "fuel": "Бензин",
    "work": "Работа",
    "psychologist": "Психолог",
    "myotherapist": "Миотерапевт",
    "other": "Разное",
}
CATEGORIES = list(CATEGORY_LABELS.values())

ACTION_LABELS = {
    "expense_add": "Добавить расход",
    "expense_sub": "Отменить расход",
    "andrei_add": "Добавить в бюджет Андрея",
    "andrei_sub": "Вычесть из бюджета Андрея",
    "max_add": "Добавить в бюджет Макса",
    "max_sub": "Вычесть из бюджета Макса",
}
