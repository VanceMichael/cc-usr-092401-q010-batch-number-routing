"""批次查询契约的服务层：规范化解析、别名单跳定位、并发安全的建号与改号。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .batch_numbers import (
    BatchNumberConflictError,
    BatchNumberNotFoundError,
    canonicalize_batch_number,
    normalize_batch_number,
)
from .models import Batch, BatchNumberAlias, Pond


@dataclass(frozen=True)
class ResolvedBatch:
    batch: Batch
    requested: str          # 规范化后的请求号
    canonical_number: str   # 批次当前的规范批次号
    via_alias: bool         # 是否经由持久化别名定位


def resolve_batch_by_number(db: Session, raw_number: str) -> ResolvedBatch:
    """按批次号唯一定位批次：先查当前号，再单跳查别名。

    别名只指向批次（永不指向别名），因此最多一跳，不存在别名循环。
    号不存在抛 BatchNumberNotFoundError（404），格式错误在规范化阶段抛 400。
    """
    requested = normalize_batch_number(raw_number)

    batch = db.query(Batch).filter(Batch.batch_number == requested).first()
    if batch is not None:
        return ResolvedBatch(batch, requested, batch.batch_number, False)

    alias = db.query(BatchNumberAlias).filter(
        BatchNumberAlias.alias == requested
    ).first()
    if alias is not None:
        batch = db.query(Batch).filter(Batch.id == alias.batch_id).first()
        if batch is not None:
            return ResolvedBatch(batch, requested, batch.batch_number, True)

    raise BatchNumberNotFoundError(
        f"批次号 {requested} 不存在", detail={"batch_number": requested}
    )


def _canonical_conflict(db: Session, canonical: str, *, exclude_batch_id: int | None = None) -> Batch | None:
    """返回占用该规范号的"其他批次"（当前号或别名），没有则 None。"""
    query = db.query(Batch).filter(Batch.batch_number == canonical)
    if exclude_batch_id is not None:
        query = query.filter(Batch.id != exclude_batch_id)
    other = query.first()
    if other is not None:
        return other
    alias = db.query(BatchNumberAlias).filter(
        BatchNumberAlias.alias == canonical
    ).first()
    if alias is not None and alias.batch_id != exclude_batch_id:
        return db.query(Batch).filter(Batch.id == alias.batch_id).first()
    return None


def assign_batch_number(db: Session, batch: Batch, raw_number: str) -> str:
    """给批次设置/更换批次号。调用方必须已持有活动事务。

    - 新号一律规范化后存储。
    - 改号时把旧号持久化为别名（旧链接继续可达）；若旧号本身是别名指向
      本批次，不重复处理。
    - 新规范号被任何别的批次（当前号或其别名）占用 -> 409，绝不一号多批。
    - 新号等于本批次当前号（仅大小写/空白差异）时，只规范化当前号，
      不产生别名。
    """
    new_canonical = normalize_batch_number(raw_number)

    if batch.batch_number is not None:
        old_canonical = canonicalize_batch_number(batch.batch_number)
    else:
        old_canonical = None

    if new_canonical == old_canonical:
        batch.batch_number = new_canonical
        db.flush()
        return new_canonical

    # 目标号被别的批次占用（含其别名）：冲突，不做任何变更
    other = _canonical_conflict(db, new_canonical, exclude_batch_id=batch.id)
    if other is not None:
        raise BatchNumberConflictError(
            f"批次号 {new_canonical} 已被批次 {other.id} 占用",
            detail={
                "batch_number": new_canonical,
                "other_batch_id": other.id,
            },
        )

    # 目标号若恰好是本批次自己的别名（改回旧号），删除该别名后复用
    own_alias = db.query(BatchNumberAlias).filter(
        BatchNumberAlias.alias == new_canonical,
        BatchNumberAlias.batch_id == batch.id,
    ).first()
    if own_alias is not None:
        db.delete(own_alias)
        db.flush()

    # 旧号落为持久化别名。旧号可能同时是本批次的别名（理论上不会，
    # 因为别名与当前号互斥），删除冲突行后再插入。
    if old_canonical is not None:
        db.query(BatchNumberAlias).filter(
            BatchNumberAlias.alias == old_canonical
        ).delete(synchronize_session=False)
        db.add(BatchNumberAlias(alias=old_canonical, batch_id=batch.id))

    batch.batch_number = new_canonical
    db.flush()
    return new_canonical


def create_batch_with_number(db: Session, *, pond_id: int, batch_number: str, **fields) -> Batch:
    """在 IMMEDIATE 事务中建批次，规范号冲突返回 409。"""
    canonical = normalize_batch_number(batch_number)

    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    occupier = db.query(Batch).filter(Batch.batch_number == canonical).first()
    if occupier is None:
        occupier_alias = db.query(BatchNumberAlias).filter(
            BatchNumberAlias.alias == canonical
        ).first()
        if occupier_alias is not None:
            occupier = db.query(Batch).filter(Batch.id == occupier_alias.batch_id).first()
    if occupier is not None:
        raise BatchNumberConflictError(
            f"批次号 {canonical} 已被批次 {occupier.id} 占用",
            detail={"batch_number": canonical, "other_batch_id": occupier.id},
        )

    batch = Batch(pond_id=pond_id, batch_number=canonical, **fields)
    db.add(batch)
    db.flush()
    return batch
