import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# все настройки и секреты лежат в .env (он не попадает в git)
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.getenv("VOICE_DATA_DIR", str(BASE_DIR / "data")))

# токен бота из BotFather
TOKEN = os.environ["TELEGRAM_TOKEN"]

# ID однозначно выбирает таблицу; имя поддерживается для старых настроек.
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "").strip()
if not SPREADSHEET_ID and not SPREADSHEET_NAME:
    raise ValueError("Set SPREADSHEET_ID or SPREADSHEET_NAME")

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
