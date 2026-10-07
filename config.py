import os

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def _packages(raw: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        credits, price = item.split(":", 1)
        result[int(credits.strip())] = int(price.strip())
    return result


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest").split(",") if m.strip()]

GEMINI_THINKING = os.getenv("GEMINI_THINKING", "low")

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

ADMIN_IDS = _ids(os.getenv("ADMIN_IDS", ""))
UNLIMITED_IDS = _ids(os.getenv("UNLIMITED_IDS", ""))
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_OWNER = os.getenv("CARD_OWNER", "")

PACKAGES = _packages(os.getenv("PACKAGES", "1:5000,3:12000,10:35000"))
FREE_CREDITS = int(os.getenv("FREE_CREDITS", "1"))
REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", "1"))
AUTO_APPROVE = os.getenv("AUTO_APPROVE", "1") == "1"
MAX_PARALLEL_JOBS = int(os.getenv("MAX_PARALLEL_JOBS", "4"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "data", "bot.db"))
TMP_DIR = os.path.join(BASE_DIR, "data", "tmp")
