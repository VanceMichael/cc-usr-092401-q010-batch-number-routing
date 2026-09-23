from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List
from urllib.parse import quote, urlencode

from ..database import get_db
from ..models import Batch, BatchName, Pond
from ..schemas import BatchCreate, BatchUpdate, BatchResponse
from ..services import batch_numbers as bn

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)

# 批次号入口的规范地址（查询参数形态，可安全携带 "/"、空格、中文）
BY_NUMBER_PATH = "/api/batches/by-number"


def _strict_quote(value, safe="/", encoding="utf-8", errors=None):
    # 忽略 urlencode 传入的 safe 默认值，所有保留字符一律百分号编码：
    # "/"→%2F、空格→%20，编码结果唯一
    return quote(str(value), safe="", encoding=encoding, errors=errors or "strict")


def _other_query(request: Request) -> list:
    """收集除 n 之外的全部查询参数（保留重复键与原值）。"""
    return [(k, v) for k, v in request.query_params.multi_items() if k != "n"]


def _query_encode(params: list) -> str:
    return urlencode(params, doseq=True, quote_via=_strict_quote)


def _by_number_location(raw_n: str, other: list, *, canonicalize: bool = True) -> str:
    """构造批次号查询地址；默认写入规范化后的 n，非法时退回原值（由目标端点判 422）。"""
    params = list(other)
    if canonicalize:
        try:
            value = bn.normalize_batch_number(raw_n)
        except bn.BatchNumberError:
            value = raw_n
    else:
        value = raw_n
    params.append(("n", value))
    return f"{BY_NUMBER_PATH}?{_query_encode(params)}"


def _resolve_or_raise(db: Session, raw_n: str, other: list):
    """批次号查询的三态解析，归一化与别名命中均 301 到唯一规范地址。"""
    try:
        canonical_form = bn.normalize_batch_number(raw_n)
    except bn.BatchNumberError as exc:
        raise HTTPException(status_code=422, detail=f"批次号格式非法: {exc}")

    if canonical_form != raw_n:
        return RedirectResponse(
            _by_number_location(raw_n, other), status_code=301
        )

    result = bn.resolve(db, raw_n)
    if result.status == bn.FOUND:
        if result.is_alias or result.canonical != raw_n:
            # 旧批次号链接：持久化别名命中，永久跳转到当前规范号
            return RedirectResponse(
                _by_number_location(result.canonical, other), status_code=301
            )
        return result.batch
    if result.status == bn.CONFLICT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"批次号规范化冲突: '{raw_n}' 不存在，但大小写不同的"
                f"'{result.matched}' 已存在；批次号大小写敏感且不得混用"
            ),
        )
    raise HTTPException(status_code=404, detail=f"批次号 '{canonical_form}' 不存在")


# ---- 静态/字面路由必须先于动态路由声明（路由顺序契约） ----

@router.get("/by-number", response_model=BatchResponse, summary="按批次号查询(规范入口)")
def get_batch_by_number(request: Request, n: str, db: Session = Depends(get_db)):
    """规范入口：GET /api/batches/by-number?n=批次号

    编码、NFC、首尾空白、大小写的唯一行为见 services.batch_numbers；
    非规范输入或历史别名 301 到规范地址；不存在 404 / 格式非法 422 / 冲突 409。
    """
    response = _resolve_or_raise(db, n, _other_query(request))
    if isinstance(response, RedirectResponse):
        return response
    return response


@router.get("/by-number/{legacy:path}", include_in_schema=False)
def legacy_get_batch_by_number(legacy: str, request: Request):
    """旧链接兼容：/by-number/{batch_number}/ 永久跳转到查询参数形态，保留查询参数。

    :path 转换器可捕获含斜杠的批次号（%2F 经网关解码后即 "/"）；
    仅去除路径制品产生的结尾斜杠。以 "/" 结尾的批次号无法从旧路径形态还原，
    请使用规范查询形态。
    """
    raw_n = legacy.rstrip("/")
    location = _by_number_location(raw_n, _other_query(request))
    return RedirectResponse(location, status_code=301)


# ---- 集合路由 ----

@router.post("/", response_model=BatchResponse)
def create_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    try:
        canonical = bn.normalize_batch_number(batch.batch_number)
    except bn.BatchNumberError as exc:
        raise HTTPException(status_code=422, detail=f"批次号格式非法: {exc}")

    try:
        new_batch = Batch(
            batch_number=canonical,
            pond_id=batch.pond_id,
            species=batch.species,
            stocking_date=batch.stocking_date,
            estimated_harvest_date=batch.estimated_harvest_date,
            actual_harvest_date=batch.actual_harvest_date,
            status=batch.status,
        )
        db.add(new_batch)
        db.flush()  # 取得 id
        bn.register_batch(db, new_batch)  # 内部加锁，冲突抛 BatchNumberConflict
        db.commit()
        db.refresh(new_batch)
        return new_batch
    except bn.BatchNumberConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"批次号 '{canonical}' 已存在")
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"批次号 '{canonical}' 已存在")


@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    batches = db.query(Batch).offset(skip).limit(limit).all()
    return batches


# ---- 数字 ID 入口：int 转换器使非纯数字（如 "by-number"）永远不可能命中 ----

@router.get("/{batch_id:int}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@router.put("/{batch_id:int}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    update_data = batch.dict(exclude_unset=True)

    if "pond_id" in update_data:
        db_pond = db.query(Pond).filter(Pond.id == update_data["pond_id"]).first()
        if not db_pond:
            raise HTTPException(status_code=404, detail="塘口不存在")

    new_number = update_data.pop("batch_number", None)
    try:
        # 先应用其它字段（session 脏检查暂存），改名事务的 commit 会一并原子提交；
        # 改名冲突 rollback 时这些暂存变更也同时回滚
        for key, value in update_data.items():
            setattr(db_batch, key, value)
        if new_number is not None:
            bn.rename_batch(db, db_batch, new_number)
        else:
            db.commit()
    except bn.BatchNumberConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"批次号 '{new_number}' 已被其他批次占用")
    except bn.BatchNumberError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"批次号格式非法: {exc}")
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="批次号冲突或数据约束失败")

    db.refresh(db_batch)
    return db_batch


@router.delete("/{batch_id:int}/")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    # 同步清理命名空间（含历史别名），避免悬挂名称
    db.query(BatchName).filter(BatchName.batch_id == batch_id).delete()
    db.delete(db_batch)
    db.commit()
    return {"message": "批次删除成功"}
