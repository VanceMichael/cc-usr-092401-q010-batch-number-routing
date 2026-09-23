from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect
from .database import engine, Base, SessionLocal
from .routers import ponds, batches, stocking, feeding, water_quality, medication, costs, harvest, analysis
from .services import batch_numbers as bn

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="水产养殖管理系统",
    description="一个完整的水产养殖管理系统，支持塘口管理、投苗记录、日常管理、成本核算、出塘销售和养殖周期分析",
    version="1.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)


@app.on_event("startup")
def _backfill_batch_names():
    """进程启动时把历史 batches.batch_number 回填到持久化命名空间表，
    保证重启后旧批次号/旧别名仍可解析。"""
    inspector = inspect(engine)
    if "batch_names" not in inspector.get_table_names():
        return
    db = SessionLocal()
    try:
        bn.backfill_names(db)
    finally:
        db.close()


@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "1.1.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
