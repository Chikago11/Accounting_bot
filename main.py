import asyncio
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
import traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    TypeHandler,
)
from app_time import now as app_now
from google_api import (
    add_expense_matrix,
    andrei_but,
    andrei_mb,
    get_month_totals,
    max_but,
    max_mb,
)
from config import ALLOWED_USER_IDS, TOKEN
from catalog import CATEGORIES, CATEGORY_LABELS, VOICE_MENU_ACTIONS, READ_ACTIONS
from errors import SheetLayoutError

MENUS = {
    "main": (
        "Выберите действие:",
        [
            ("Добавить расход", "add"),
            ("Отменить расход", "sub"),
            ("Отчет за месяц", "report"),
            ("Отдельные бюджеты", "budgets"),
        ],
    ),
    "budgets": (
        "Отдельные бюджеты. Выберите бюджет:",
        [
            ("Бюджет Андрея", "andrei"),
            ("Бюджет Макса", "max"),
            ("← Назад", "main"),
        ],
    ),
    "andrei": (
        "Бюджет Андрея. Выберите действие:",
        [
            ("Добавить", "andrei_add"),
            ("Вычесть", "andrei_sub"),
            ("Показать", "andrei_but"),
            ("← Назад", "budgets"),
        ],
    ),
    "max": (
        "Бюджет Макса. Выберите действие:",
        [
            ("Добавить", "max_add"),
            ("Вычесть", "max_sub"),
            ("Показать", "max_but"),
            ("← Назад", "budgets"),
        ],
    ),
}


def menu_keyboard(menu="main"):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=f"menu:{action}")]
            for label, action in MENUS[menu][1]
        ]
    )


def back_keyboard(destination):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("← Назад", callback_data=f"menu:{destination}")]]
    )


async def reply_or_edit(update, text, reply_markup=None, *, new_message=False):
    if update.callback_query and not new_message:
        message = update.effective_message
        if message.text == text and message.reply_markup == reply_markup:
            return
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


# Пускаем к боту только тех, чьи ID есть в ALLOWED_USER_IDS в .env
async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and user.id in ALLOWED_USER_IDS:
        return

    user_id = user.id if user else None
    print(
        f"Access denied: id={user_id} name={user.full_name if user else None}",
        flush=True,
    )

    if update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(f"Нет доступа. Ваш Telegram ID: {user_id}")

    # остальные обработчики не запускаем
    raise ApplicationHandlerStop


# Функция для команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user  # достаём объект пользователя
    name = user.first_name  # берём его имя (как в Телеграме)
    context.user_data.update(mode=None, category=None, menu="main")

    await update.message.reply_text(
        f"Привет, {name}! Рад тебя видеть 👋\n"
        "Я твой бот для учёта бюджета 🤑\n"
        + (
            "Голосом можно управлять расходами и бюджетами, смотреть остатки и отчёты, открывать меню.\n"
            if context.bot_data.get("voice")
            else ""
        )
        + "\nВыберите действие:",
        reply_markup=menu_keyboard(),
    )


# функция отправки пользователю категорий для добавления расхода
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="add", category=None, menu="main")

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"expense:add:{category_id}")]
        for category_id, label in CATEGORY_LABELS.items()
    ]
    keyboard.append([InlineKeyboardButton("← Назад", callback_data="menu:main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await reply_or_edit(
        update,
        "Выберите категорию, в которую хотите ДОБАВИТЬ расход:",
        reply_markup=reply_markup,
    )


# функция отправки пользователю категорий для удаления расхода
async def sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="sub", category=None, menu="main")

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"expense:sub:{category_id}")]
        for category_id, label in CATEGORY_LABELS.items()
    ]
    keyboard.append([InlineKeyboardButton("← Назад", callback_data="menu:main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await reply_or_edit(
        update, "Выберите категорию, из которой хотите ОТНЯТЬ сумму:", reply_markup=reply_markup
    )


# функция выбор категории
async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получаем объект callback-запроса
    query = update.callback_query

    # Обязательно отвечаем на callback, иначе у пользователя будет "крутиться часик"
    await query.answer()

    # Действие хранится в кнопке, чтобы старые сообщения не путали режимы.
    data = query.data or ""
    if data.startswith("expense:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, mode, category = parts
    else:
        # Поддержка кнопок категорий, отправленных до появления меню.
        category = data
        mode = context.user_data.get("mode")
        if mode not in ("add", "sub"):
            context.user_data.update(mode=None, category=None)
            await reply_or_edit(update, "Выберите действие в меню:", reply_markup=menu_keyboard())
            return

    # Old messages contain the label; new messages contain the stable category ID.
    category = CATEGORY_LABELS.get(category, category)
    if category not in CATEGORIES or mode not in ("add", "sub"):
        return

    await prompt_expense_amount(update, context, mode, category)


async def prompt_expense_amount(update, context, mode, category):
    context.user_data.update(category=category, mode=mode, menu="main")

    if mode == "sub":
        action_text = "которую хотите ОТНЯТЬ"
    else:
        action_text = "которую хотите ДОБАВИТЬ"

    await reply_or_edit(
        update,
        f"Категория: {category}\nТеперь отправь сумму, {action_text}, например: 250",
        reply_markup=back_keyboard(mode),
    )


async def add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.strip().replace(",", ".")

    # Пытаемся преобразовать текст в число (разрешим дробные суммы)
    try:
        amount = Decimal(text)
        if (
            not amount.is_finite()
            or not 0 < amount <= Decimal("10000000")
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise ValueError("Invalid amount")
    except (ValueError, InvalidOperation):
        await update.message.reply_text(
            "Введите положительную сумму до 10 000 000 с максимум двумя знаками после запятой, "
            "например 250 или 250.75."
        )
        return

    # Личный бюджет Андрея или Макса
    mode = context.user_data.get("mode")
    if mode in ("add_amb", "sub_amb", "add_mmb", "sub_mmb"):
        if mode in ("add_mmb", "sub_mmb"):
            update_budget, owner = max_mb, "Макса"
            budget_menu = "max"
        else:
            update_budget, owner = andrei_mb, "Андрея"
            budget_menu = "andrei"

        if await asyncio.to_thread(update_budget, mode, abs(amount)):
            if mode in ("sub_amb", "sub_mmb"):
                await update.message.reply_text(
                    f"Отнял {abs(amount)} из бюджета {owner} 👍",
                    reply_markup=menu_keyboard(budget_menu),
                )
            else:
                await update.message.reply_text(
                    f"Записал {abs(amount)} в бюджет {owner} 👍",
                    reply_markup=menu_keyboard(budget_menu),
                )

        context.user_data.update(mode=None, category=None)
        return

    # Берём выбранную категорию из user_data
    category = context.user_data.get("category")

    # Если категории нет — значит, пользователь ещё не нажимал кнопку
    if not category:
        await update.message.reply_text(
            "Сначала выбери действие в меню или категорию через /add 🙂",
            reply_markup=menu_keyboard(),
        )
        return

    mode = context.user_data.get("mode", "add")
    if mode == "sub":
        amount = -amount

    # Запись в Google Sheets
    await asyncio.to_thread(add_expense_matrix, category, amount)

    if mode == "sub":
        await update.message.reply_text(
            f"Отнял {abs(amount)} из категории «{category}» 👍",
            reply_markup=menu_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"Записал {amount} в категорию «{category}» 👍",
            reply_markup=menu_keyboard(),
        )

    # сбрасываем категорию и режим
    context.user_data.update(mode=None, category=None)


async def report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, month_key=None, *, new_message=False
):
    context.user_data.update(mode=None, category=None, menu="main")
    month_key = month_key or app_now().strftime("%Y-%m")
    totals = await asyncio.to_thread(get_month_totals, month_key)

    if not totals:
        await reply_or_edit(
            update,
            f"За {month_key} пока нет данных 🙂",
            reply_markup=menu_keyboard(),
            new_message=new_message,
        )
        return

    lines = [f"📊 Отчёт за {month_key}:\n"]

    for category, amount in totals.items():
        lines.append(f"• {category}: {round(amount, 2)}")

    await reply_or_edit(
        update, "\n".join(lines), reply_markup=menu_keyboard(), new_message=new_message
    )


async def andrei_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="add_amb", category=None, menu="andrei")

    await reply_or_edit(
        update,
        "Какую сумму добавить в бюджет Андрея, например: 250",
        reply_markup=back_keyboard("andrei"),
    )


async def andrei_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="sub_amb", category=None, menu="andrei")

    await reply_or_edit(
        update,
        "Какую сумму отнять из бюджета Андрея, например: 250",
        reply_markup=back_keyboard("andrei"),
    )


async def andrei_but_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, new_message=False
):
    context.user_data.update(mode=None, category=None, menu="andrei")

    await reply_or_edit(
        update,
        f"Бюджет Андрея: {await asyncio.to_thread(andrei_but)} THB",
        reply_markup=menu_keyboard("andrei"),
        new_message=new_message,
    )


async def max_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="add_mmb", category=None, menu="max")

    await reply_or_edit(
        update,
        "Какую сумму добавить в бюджет Макса, например: 250",
        reply_markup=back_keyboard("max"),
    )


async def max_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.update(mode="sub_mmb", category=None, menu="max")

    await reply_or_edit(
        update,
        "Какую сумму отнять из бюджета Макса, например: 250",
        reply_markup=back_keyboard("max"),
    )


async def max_but_command(update: Update, context: ContextTypes.DEFAULT_TYPE, *, new_message=False):
    context.user_data.update(mode=None, category=None, menu="max")

    await reply_or_edit(
        update,
        f"Бюджет Макса: {await asyncio.to_thread(max_but)} THB",
        reply_markup=menu_keyboard("max"),
        new_message=new_message,
    )


async def menu_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.removeprefix("menu:")
    await run_menu_action(action, update, context)


async def run_menu_action(action, update, context):
    if action in MENUS:
        context.user_data.update(mode=None, category=None, menu=action)
        await reply_or_edit(update, MENUS[action][0], reply_markup=menu_keyboard(action))
        return

    commands = {
        "add": add_command,
        "sub": sub_command,
        "report": report,
        "andrei_add": andrei_add_command,
        "andrei_sub": andrei_sub_command,
        "andrei_but": andrei_but_command,
        "max_add": max_add_command,
        "max_sub": max_sub_command,
        "max_but": max_but_command,
    }
    handler = commands.get(action)
    if handler:
        await handler(update, context)


async def run_voice_command(operation, update, context):
    """Voice uses the same menu/read handlers as Telegram buttons."""
    action = operation.action
    if action in READ_ACTIONS:
        if action == "report":
            await report(update, context, operation.occurred_at[:7], new_message=True)
        elif action == "andrei_show":
            await andrei_but_command(update, context, new_message=True)
        else:
            await max_but_command(update, context, new_message=True)
    elif action in ("expense_add_prompt", "expense_sub_prompt") and operation.category_id:
        await prompt_expense_amount(
            update,
            context,
            "add" if action == "expense_add_prompt" else "sub",
            CATEGORY_LABELS[operation.category_id],
        )
    else:
        await run_menu_action(VOICE_MENU_ACTIONS[action], update, context)


def log_safe_error(event, error):
    # Only code locations, never exception text, source lines or local variables.
    locations = " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(error.__traceback__, limit=-5)
    )
    logging.getLogger(__name__).error(
        "%s code=%s at=%s", event, type(error).__name__, locations or "unknown"
    )


async def on_error(update, context):
    log_safe_error("bot_error", context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"Операция не записана. {context.error}"
                if isinstance(context.error, SheetLayoutError)
                else "Не удалось завершить действие. Если шла запись, проверьте таблицу перед повторным вводом.",
                reply_markup=menu_keyboard(),
            )
        except Exception as error:
            log_safe_error("error_reply_failed", error)


async def voice_disabled(update, context):
    text = "Голосовой ввод сейчас отключён. Используйте кнопки меню."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    else:
        await update.effective_message.reply_text(text, reply_markup=menu_keyboard())


def build_application():

    # Создаём приложение (бота)
    app = Application.builder().token(TOKEN).build()

    # Проверка доступа срабатывает раньше всех остальных обработчиков
    app.add_handler(TypeHandler(Update, check_access), group=-1)

    # Добавляем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("sub", sub_command))
    app.add_handler(CommandHandler("andrei_add", andrei_add_command))
    app.add_handler(CommandHandler("andrei_sub", andrei_sub_command))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("andrei_but", andrei_but_command))
    app.add_handler(CommandHandler("max_add", max_add_command))
    app.add_handler(CommandHandler("max_sub", max_sub_command))
    app.add_handler(CommandHandler("max_but", max_but_command))

    from voice.settings import VoiceSettings

    settings = VoiceSettings.from_env()
    if settings.enabled:
        from transactions import execute_operation
        from voice.handler import VoiceController
        from voice.providers import HermesBridge
        from voice.store import PendingStore

        bridge = HermesBridge(settings)
        store = PendingStore(settings.data_dir / "voice.sqlite3", settings.ttl)
        voice = VoiceController(
            settings,
            store,
            bridge,
            bridge,
            execute_operation,
            ALLOWED_USER_IDS,
            menu_keyboard,
            command_handler=run_voice_command,
        )
        voice.acquire_worker()
        app.bot_data["voice"] = voice
        logging.getLogger(__name__).info(
            "Voice input enabled; model=%s; transport=%s; durable confirmation store ready",
            settings.hermes_model or "Hermes default",
            "http" if settings.bridge_url else "command",
        )
        app.add_handler(MessageHandler(filters.VOICE, voice.receive, block=False))
        app.add_handler(CallbackQueryHandler(voice.callback, pattern=r"^voice:"))
    else:
        app.add_handler(MessageHandler(filters.VOICE, voice_disabled))
        app.add_handler(CallbackQueryHandler(voice_disabled, pattern=r"^voice:"))

    # кнопки
    app.add_handler(CallbackQueryHandler(menu_selected, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(category_chosen))

    # текст (сумма)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_amount))
    app.add_error_handler(on_error)
    return app


# Главная функция, запускающая бота
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = build_application()

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("Bot started...")

    # держим процесс живым
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
