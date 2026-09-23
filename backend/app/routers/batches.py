from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from typing import List
import time

from ..database import get_db, get_write_db
from ..models import Batch, Pond
from ..schemas import BatchCreate, BatchUpdate, BatchResponse
from ..batch_numbers import (
    BatchNumberConflictError,
    BatchNumberNotFoundError,
    InvalidBatchNumberError,
    canonical_url,
    has_variation,
    normalize_batch_number,
)
from ..batch_service import assign_batch_number, create_batch_with_number, resolve_batch_by_number

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)

# 批次号入口的唯一规范地址：batch_number 只走 query 参数，
# 与按数字 ID 的 /{batch_id} 路径在词法层面永不相交。
BY_NUMBER_CANONICAL_PATH = "/api/batches/by-number"

_SQLITE_LOCK_RETRIES = 5


def _is_locked(exc: OperationalError) -> bool:
    orig = getattr(exc, "orig", None)
    return orig is not None and "database is locked" in str(orig).lower()


@router.post("/", response_model=BatchResponse)
def create_batch(batch: BatchCreate, db: Session = Depends(get_write_db)):
    rest = batch.dict(exclude={"batch_number", "pond_id"})
    last_locked: OperationalError | None = None
    for attempt in range(_SQLITE_LOCK_RETRIES):
        try:
            with db.begin():
                new_batch = create_batch_with_number(
                    db,
                    pond_id=batch.pond_id,
                    batch_number=batch.batch_number,
                    **rest,
                )
            db.refresh(new_batch)
            return new_batch
        except IntegrityError:
            # 唯一约束兜底：并发建号撞车在数据库层被拒绝
            raise BatchNumberConflictError(
                "批次号与已有批次或别名冲突",
                detail={"batch_number": normalize_batch_number(batch.batch_number)},
            )
        except OperationalError as exc:
            if not _is_locked(exc):
                raise
            last_locked = exc
            time.sleep(0.05 * (attempt + 1))
    assert last_locked is not None
    raise HTTPException(status_code=503, detail="系统繁忙，请稍后重试")


@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    batches = db.query(Batch).offset(skip).limit(limit).all()
    return batches


# 注意：批次号入口（静态前缀 /by-number）必须在 /{batch_id} 动态路由之前
# 注册。两者路径形状不同（一个是静态前缀 + query，一个是 int 段），
# 加上顺序保证，ID 入口与批次号入口互不遮蔽（见回归测试）。
@router.get("/by-number", response_model=BatchResponse)
def get_batch_by_number(request: Request, batch_number: str | None = None, db: Session = Depends(get_db)):
    """按批次号查询的唯一规范入口。

    - 200：输入即规范形态且命中当前批次号。
    - 301：输入存在规范化差异（大小写/首尾空白/NFKC）或命中持久化别名，
      跳转到当前规范号地址，查询参数原样保留。
    - 400：缺参或格式非法；404：规范号不存在。
    """
    raw = batch_number
    if raw is None:
        raise InvalidBatchNumberError("缺少 batch_number 查询参数")

    canonical = normalize_batch_number(raw)  # 400 on invalid
    resolved = resolve_batch_by_number(db, raw)  # 404 on miss
    target_number = resolved.canonical_number

    if has_variation(raw) or resolved.via_alias or target_number != canonical:
        # 统一收敛到唯一规范地址（含别名改号后的当前号）
        query = {k: v for k, v in request.query_params.items() if k != "batch_number"}
        return RedirectResponse(
            canonical_url(BY_NUMBER_CANONICAL_PATH, target_number, query),
            status_code=301,
        )
    return resolved.batch


@router.get("/by-number/{batch_number}/", include_in_schema=False)
def legacy_get_batch_by_number(request: Request, batch_number: str, db: Session = Depends(get_db)):
    """旧链接兼容：/by-number/{号}/ 一律 301 到规范 query 地址并保留参数。

    无论号是否存在都先迁移地址形状；存在性由规范入口判定（404）。
    若该号是改号前的旧别名，直接跳到当前规范号，避免二次跳转。
    """
    canonical = normalize_batch_number(batch_number)  # 400 on invalid
    target_number = canonical
    try:
        target_number = resolve_batch_by_number(db, canonical).canonical_number
    except BatchNumberNotFoundError:
        # 不存在时仍迁移到规范地址，由规范入口返回 404
        pass
    query = {k: v for k, v in request.query_params.items() if k != "batch_number"}
    return RedirectResponse(
        canonical_url(BY_NUMBER_CANONICAL_PATH, target_number, query),
        status_code=301,
    )


@router.get("/{batch_id}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@router.put("/{batch_id}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_write_db)):
    update_data = batch.dict(exclude_unset=True)
    new_number_raw = update_data.pop("batch_number", None)

    last_locked: OperationalError | None = None
    for attempt in range(_SQLITE_LOCK_RETRIES):
        try:
            # with 块抛任何异常都会回滚整个事务，包括 404/409 前置校验
            with db.begin():
                db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
                if not db_batch:
                    raise HTTPException(status_code=404, detail="批次不存在")

                if "pond_id" in update_data:
                    db_pond = db.query(Pond).filter(Pond.id == update_data["pond_id"]).first()
                    if not db_pond:
                        raise HTTPException(status_code=404, detail="塘口不存在")

                if new_number_raw is not None:
                    # 规范化 + 旧号落别名 + 占用冲突检测，全部在同一写事务内
                    assign_batch_number(db, db_batch, new_number_raw)

                for key, value in update_data.items():
                    setattr(db_batch, key, value)
                db.flush()
            db.refresh(db_batch)
            return db_batch
        except IntegrityError:
            # 唯一约束兜底：并发下的一号多批在数据库层被拒绝（事务已回滚）
            canonical = normalize_batch_number(new_number_raw) if new_number_raw is not None else None
            raise BatchNumberConflictError(
                "批次号与已有批次或别名冲突",
                detail={"batch_number": canonical},
            )
        except OperationalError as exc:
            if not _is_locked(exc):
                raise
            last_locked = exc
            time.sleep(0.05 * (attempt + 1))
    assert last_locked is not None
    raise HTTPException(status_code=503, detail="系统繁忙，请稍后重试")


@router.delete("/{batch_id}/")
def delete_batch(batch_id: int, db: Session = Depends(get_write_db)):
    with db.begin():
        db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not db_batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        db.delete(db_batch)  # 别名经 cascade 一并清理
    return {"message": "批次删除成功"}
