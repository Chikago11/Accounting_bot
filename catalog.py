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
WRITE_ACTIONS = frozenset(ACTION_LABELS)

# Every menu target has a voice equivalent. Tests check this against main.MENUS.
VOICE_MENU_ACTIONS = {
    "menu_main": "main",
    "menu_budgets": "budgets",
    "menu_andrei": "andrei",
    "menu_max": "max",
    "expense_add_prompt": "add",
    "expense_sub_prompt": "sub",
    "andrei_add_prompt": "andrei_add",
    "andrei_sub_prompt": "andrei_sub",
    "max_add_prompt": "max_add",
    "max_sub_prompt": "max_sub",
    "report": "report",
    "andrei_show": "andrei_but",
    "max_show": "max_but",
}
READ_ACTIONS = frozenset({"report", "andrei_show", "max_show"})
NAVIGATION_ACTIONS = frozenset(VOICE_MENU_ACTIONS) - READ_ACTIONS
ACTION_LABELS.update(
    {
        "menu_main": "Главное меню / назад в главное меню",
        "menu_budgets": "Меню отдельных бюджетов",
        "menu_andrei": "Меню бюджета Андрея",
        "menu_max": "Меню бюджета Макса",
        "expense_add_prompt": "Добавить расход: выбрать категорию или запросить сумму",
        "expense_sub_prompt": "Отменить расход: выбрать категорию или запросить сумму",
        "andrei_add_prompt": "Добавить в бюджет Андрея: запросить сумму",
        "andrei_sub_prompt": "Вычесть из бюджета Андрея: запросить сумму",
        "max_add_prompt": "Добавить в бюджет Макса: запросить сумму",
        "max_sub_prompt": "Вычесть из бюджета Макса: запросить сумму",
        "report": "Показать отчёт за месяц",
        "andrei_show": "Показать остаток бюджета Андрея",
        "max_show": "Показать остаток бюджета Макса",
    }
)
