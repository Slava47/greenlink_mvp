import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "app.db"))
    SEED_ON_FIRST_RUN = os.getenv("SEED_ON_FIRST_RUN", "1") == "1"
    BASE_URL = os.getenv("BASE_URL", "")
