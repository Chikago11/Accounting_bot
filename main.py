# main.py
import asyncio
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
from datetime import datetime
from google_api import add_expense_matrix, andrei_but, andrei_mb, get_month_totals
from config import ALLOWED_USER_IDS, TOKEN

# edited on GitHub for test pull git
# PR: улучшен стартовый комментарий test5
# test direct commit to master

CATEGORIES = [
    "Дом, телефон, интернет",
    "Супермаркет",
    "Школа Макс",
    "Секции Макс",
    "Страховка",
    "Виза",
    "Медицина",
    "Машина/Байк",
    "Бензин",
    "Работа",
    "Психолог",
    "Миотерапевт",
    "Разное",
]


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
        await update.effective_message.reply_text(
            f"Нет доступа. Ваш Telegram ID: {user_id}"
        )

    # остальные обработчики не запускаем
    raise ApplicationHandlerStop


# Функция для команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user  # достаём объект пользователя
    name = user.first_name  # берём его имя (как в Телеграме)

    await update.message.reply_text(
        f"Привет, {name}! Рад тебя видеть 👋\n" f"Я твой бот для учёта бюджета 🤑"
    )


# функция отправки пользователю категорий для добавления расхода
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "add"  # режим добавления

    keyboard = [[InlineKeyboardButton(cat, callback_data=cat)] for cat in CATEGORIES]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Выберите категорию, в которую хотите ДОБАВИТЬ расход:",
        reply_markup=reply_markup,
    )


# функция отправки пользователю категорий для удаления расхода
async def sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "sub"  # режим вычитания

    keyboard = [[InlineKeyboardButton(cat, callback_data=cat)] for cat in CATEGORIES]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Выберите категорию, из которой хотите ОТНЯТЬ сумму:", reply_markup=reply_markup
    )


# функция выбор категории
async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получаем объект callback-запроса
    query = update.callback_query

    # Обязательно отвечаем на callback, иначе у пользователя будет "крутиться часик"
    await query.answer()

    # В callback_data мы положили название категории
    category = query.data

    # Сохраняем выбранную категорию в user_data для этого пользователя
    context.user_data["category"] = category

    mode = context.user_data.get("mode", "add")
    if mode == "sub":
        action_text = "которую хотите ОТНЯТЬ"
    else:
        action_text = "которую хотите ДОБАВИТЬ"

    await query.edit_message_text(
        text=f"Категория: {category}\nТеперь отправь сумму, {action_text}, например: 250"
    )


async def add_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.strip().replace(",", ".")

    # Пытаемся преобразовать текст в число (разрешим дробные суммы)
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введи сумму числом, например 250 или 250.75"
        )
        return

    # Режим "бюджет Андрея" (если пользователь вызвал /andrei_add или /andrei_sub)
    mode = context.user_data.get("mode")
    if mode in ("add_amb", "sub_amb"):
        if mode == "sub_amb":
            amount = -abs(amount)

        if andrei_mb(mode, abs(amount)):
            if mode == "sub_amb":
                await update.message.reply_text(
                    f"Отнял {abs(amount)} из бюджета Андрея 👍"
                )
            else:
                await update.message.reply_text(
                    f"Записал {abs(amount)} в бюджет Андрея 👍"
                )

        context.user_data["mode"] = None
        return

    # Берём выбранную категорию из user_data
    category = context.user_data.get("category")

    # Если категории нет — значит, пользователь ещё не нажимал кнопку
    if not category:
        await update.message.reply_text(
            "Сначала выбери категорию через команду /add 🙂"
        )
        return

    mode = context.user_data.get("mode", "add")
    if mode == "sub":
        amount = -amount

    # Запись в Google Sheets
    add_expense_matrix(category, amount)

    if mode == "sub":
        await update.message.reply_text(
            f"Отнял {abs(amount)} из категории «{category}» 👍"
        )
    else:
        await update.message.reply_text(f"Записал {amount} в категорию «{category}» 👍")

    # сбрасываем категорию и режим
    context.user_data["category"] = None
    context.user_data["mode"] = "add"


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month_key = datetime.now().strftime("%Y-%m")
    totals = get_month_totals(month_key)

    if not totals:
        await update.message.reply_text("За этот месяц пока нет данных 🙂")
        return

    lines = [f"📊 Отчёт за {month_key}:\n"]

    for category, amount in totals.items():
        lines.append(f"• {category}: {round(amount, 2)}")

    await update.message.reply_text("\n".join(lines))


async def andrei_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Какую сумму добавить в бюджет Андрея, например: 250"
    )

    context.user_data["mode"] = "add_amb"


async def andrei_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Какую сумму отнять из бюджета Андрея, например: 250"
    )

    context.user_data["mode"] = "sub_amb"

async def andrei_but_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        
    await update.message.reply_text(
        f"Бюджет Андрея: {andrei_but()} THB"
    )   


# Главная функция, запускающая бота
async def main():

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

    # кнопки
    app.add_handler(CallbackQueryHandler(category_chosen))

    # текст (сумма)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_amount))

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


if __name__ == "__main__":
    asyncio.run(main())
