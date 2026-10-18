import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# все настройки и секреты лежат в .env (он не попадает в git)
load_dotenv(BASE_DIR / ".env")

# токен бота из BotFather
TOKEN = os.environ["TELEGRAM_TOKEN"]

# имя таблицы в Google Sheets
SPREADSHEET_NAME = os.environ["SPREADSHEET_NAME"]

# 🔑 Путь к JSON-файлу сервисного аккаунта Google
GOOGLE_SERVICE_ACCOUNT_FILE = BASE_DIR / os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"
)

# Telegram ID тех, кому можно пользоваться ботом
ALLOWED_USER_IDS = {
    int(user_id)
    for user_id in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",")
    if user_id
}
