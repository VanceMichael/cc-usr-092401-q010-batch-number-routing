from sqlalchemy import create_engine, event, Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./aquaculture.db"
)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
    if db_path:
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)


def configure_sqlite_engine(engine: Engine, *, immediate: bool = False) -> None:
    """SQLite 完整性配置；immediate=True 时所有事务用 BEGIN IMMEDIATE。

    - 读引擎保持 SQLite 默认的延迟事务：纯读只拿 SHARED 锁，读与读可并发。
    - 写引擎立即拿 RESERVED 锁：并发改号/建号在第一条语句即排队串行化，
      配合唯一约束杜绝一号多批，也避免写锁升级导致的死锁。
    两类连接通过同一个数据库文件的锁语义协同。
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _sqlite_connect(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        # WAL：写事务提交期间读请求不被阻塞；外键强制开启；锁等待 5s
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
        if immediate:
            # 关闭 pysqlite 隐式 BEGIN，交由 begin 事件发 BEGIN IMMEDIATE
            dbapi_connection.isolation_level = None

    if immediate:
        @event.listens_for(engine, "begin")
        def _sqlite_begin_immediate(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")


def make_engine(url: str, *, immediate: bool = False) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine, immediate=immediate)
    return engine


# 默认引擎：读多写少场景下的读连接（延迟事务）
engine = make_engine(SQLALCHEMY_DATABASE_URL)
# 写引擎：所有改号/建号/删除走它，BEGIN IMMEDIATE 串行化写事务
write_engine = make_engine(SQLALCHEMY_DATABASE_URL, immediate=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
WriteSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=write_engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_write_db():
    """写请求专用依赖：绑定 IMMEDIATE 引擎，第一条语句即取写锁。"""
    db = WriteSessionLocal()
    try:
        yield db
    finally:
        db.close()
