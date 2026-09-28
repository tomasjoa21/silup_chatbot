import os
from pathlib import Path
from dotenv import load_dotenv

# 기본 경로
BASE_DIR = Path(__file__).resolve().parent.parent

# .env 로드
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "silup.db")))
RULES_PATH = Path(os.getenv("RULES_PATH", str(BASE_DIR / "knowledge" / "rules.json")))
KNOWLEDGE_PATH = Path(os.getenv("KNOWLEDGE_PATH", str(BASE_DIR / "knowledge" / "knowledge_base.md")))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")

# data 디렉토리 자동 생성
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
