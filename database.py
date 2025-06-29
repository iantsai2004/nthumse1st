# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

# 加載 .env 檔中的環境變數
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """依賴注入用的資料庫會話"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 示例：初始化資料庫 (在 app.py 中調用)
def init_db():
    # 這裡可以導入所有模型，然後調用 Base.metadata.create_all(engine)
    # 為了避免循環引用，會在 models.py 中導入 Base
    print("Initializing database...")
    from models import Team, Card, TeamCard, AdminPassword, TradeRequest # 確保所有模型都被導入
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")

if __name__ == '__main__':
    # 這部分只用於測試或手動初始化資料庫
    init_db()
    print("Database initialization script executed.")