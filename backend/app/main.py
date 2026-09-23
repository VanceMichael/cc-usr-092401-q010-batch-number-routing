from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .database import engine, Base
from .batch_numbers import BatchNumberError
from .startup import normalize_existing_batch_numbers
from .routers import ponds, batches, stocking, feeding, water_quality, medication, costs, harvest, analysis

Base.metadata.create_all(bind=engine)
# 存量批次号收敛到唯一规范形态（幂等；存在会互相合并的冲突时拒绝启动）
normalize_existing_batch_numbers(engine)

app = FastAPI(
    title="水产养殖管理系统",
    description="一个完整的水产养殖管理系统，支持塘口管理、投苗记录、日常管理、成本核算、出塘销售和养殖周期分析",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(BatchNumberError)
def batch_number_error_handler(request: Request, exc: BatchNumberError):
    """批次号契约错误的唯一出口：400/404/409 带稳定机器码，前端据此区分。"""
    content = {"detail": exc.message, "code": exc.code}
    content.update(exc.detail)
    return JSONResponse(status_code=exc.status_code, content=content)

app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)

@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "1.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
