"""Налаштування. Значення ключів беруться ТІЛЬКИ з Railway → Variables, у коді їх немає."""
import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Telegram ID власниці (можна кілька через кому). Дізнатися свій ID: напишіть агенту /myid.
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_ID", "").replace(" ", "").split(",") if x.strip().isdigit()]
DB_PATH = os.environ.get("DB_PATH", "bot.db")  # на Railway: /data/bot.db (диск Volume у /data)
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Kyiv"))


def is_admin(uid):
    return uid in ADMIN_IDS
