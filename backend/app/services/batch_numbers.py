"""批次号契约：编码、Unicode 规范化、空白、大小写与持久化别名的唯一实现。

唯一行为约定（前后端共用同一套规则，前端另有一份等价 TS 实现）：
  1. URL 中的原始百分号编码由 ASGI 网关（Starlette）按 UTF-8 解码；
     批次号入口只接受查询参数（query string），因此可安全携带 "/"、空格、中文；
  2. Unicode 统一规范化为 NFC；
  3. 去除首尾空白（strip，含全角空格等 Unicode 空白）；
  4. 大小写敏感 —— "ABC2024" 与 "abc2024" 是两个不同的批次号，绝不合并；
  5. 不允许控制字符；规范化后的长度为 1..50。
     "/" 与 "%" 允许出现——批次号入口使用查询参数而非路径段，
     生成地址时统一百分号编码（"/"→%2F、空格→%20），解码只发生一次，
     浏览器与服务端结果一致；旧式路径链接仅做兼容跳转，不参与规范形式。

查找语义（resolve）：
  精确命中（NFC/大小写敏感）→ FOUND
  大小写折叠后命中         → CONFLICT（存在规范化冲突，区别于"找不到"）
  其它                     → NOT_FOUND

别名模型：BatchName 表中 current 与 alias 共用同一全局唯一命名空间，
所以名称永远只指向一个批次，且"别名指向名称"的链结构在表中不存在，
别名循环在数据模型层面不可能发生（解析永远是一次表查询，零跳）。
"""

from __future__ import annotations

import threading
import unicodedata
from collections import namedtuple
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Batch, BatchName

MAX_LENGTH = 50

FOUND = "found"
NOT_FOUND = "not_found"
CONFLICT = "conflict"

# status: found/not_found/conflict；batch: 命中的批次；canonical: 当前号；
# matched: 实际命中名称（可能是别名）；is_alias: 命中名称是否为历史别名
Resolution = namedtuple("Resolution", "status batch canonical matched is_alias")


class BatchNumberError(ValueError):
    """批次号格式非法。"""


class BatchNumberConflict(Exception):
    """目标名称已指向另一个批次（一号多批冲突）。"""


def normalize_batch_number(raw: object) -> str:
    """把任意输入归一化为唯一的批次号表示；非法时抛出 BatchNumberError。

    规范化步骤固定为：str → NFC → strip → 控制字符/分隔符/长度校验。
    不做大小写折叠。
    """
    if raw is None:
        raise BatchNumberError("批次号不能为空")
    try:
        text = str(raw)
    except Exception as exc:  # pragma: no cover - 防御性
        raise BatchNumberError("批次号必须是字符串") from exc

    # 先 NFC 再 strip：某些兼容性字符在规范化后才会暴露为空白
    text = unicodedata.normalize("NFC", text)
    text = text.strip()

    if not text:
        raise BatchNumberError("批次号不能为空")
    if len(text) > MAX_LENGTH:
        raise BatchNumberError(f"批次号长度不能超过 {MAX_LENGTH} 个字符")
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):  # 控制字符（Cc/Cf/Cs/Co/Cn）
            raise BatchNumberError("批次号不能包含控制字符")
    return text


def needs_redirect(raw: object) -> bool:
    """原始输入与规范形式是否不同（决定是否需要 301 归一化）。"""
    if raw is None:
        return False
    return str(raw) != normalize_batch_number(raw)


def _fold(name: str) -> str:
    """用于冲突检测的大小写折叠（不参与存储与匹配，仅检测）。"""
    return name.casefold()


# 串行化所有改名/创建事务，避免两个并发请求把同一新号分配给不同批次、
# 或在同一事务窗口里丢失旧别名。配合数据库唯一约束形成双保险。
_rename_lock = threading.RLock()


def _get_name_row(db: Session, name: str) -> Optional[BatchName]:
    return db.query(BatchName).filter(BatchName.name == name).first()


def resolve(db: Session, raw: object) -> Resolution:
    """按批次号定位批次（当前号或历史别名均可）。

    返回 Resolution(status, batch, canonical, matched, is_alias)：
      FOUND      精确命中；batch 为批次，canonical 为当前号，matched 为实际命中名
      NOT_FOUND  格式化合法但不存在（batch/canonical/matched 均为 None）
      CONFLICT   仅大小写不同地命中了别的批次号（batch 为那个批次）
    非法格式直接抛 BatchNumberError（路由层转 422）。
    """
    name = normalize_batch_number(raw)

    row = _get_name_row(db, name)
    if row is not None:
        batch = db.query(Batch).filter(Batch.id == row.batch_id).first()
        canonical = batch.batch_number if batch else None
        return Resolution(FOUND, batch, canonical, name, row.kind == "alias")

    # 大小写折叠冲突检测：只在精确未命中时扫描一次
    folded = _fold(name)
    for cand in db.query(BatchName).all():
        if _fold(cand.name) == folded:
            other = db.query(Batch).filter(Batch.id == cand.batch_id).first()
            return Resolution(CONFLICT, other, None, cand.name, False)

    return Resolution(NOT_FOUND, None, None, None, False)


def backfill_names(db: Session) -> int:
    """启动时（或旧库升级时）把 batches.batch_number 回填进命名空间表，
    保证重启后旧批次号/旧别名仍可解析。

    也修复"旁路 SQL 直接改过 batches.batch_number"的情况：
    该批次名下旧的 current 行降级为 alias，新号提升/登记为 current。
    若新号已指向别的批次（历史脏数据），跳过该行，绝不静默合并。
    """
    created = 0
    with _rename_lock:
        batches = db.query(Batch).order_by(Batch.id).all()
        for batch in batches:
            canonical = normalize_batch_number(batch.batch_number)
            if batch.batch_number != canonical:
                batch.batch_number = canonical

            target = _get_name_row(db, canonical)
            if target is not None and target.batch_id != batch.id:
                # 名称已属于别的批次：不合并，留给运维处理
                continue
            if target is None:
                db.add(BatchName(name=canonical, kind="current", batch_id=batch.id))
                created += 1
            elif target.kind != "current":
                target.kind = "current"

            # 该批次的其它名称若有多个 current（脏数据），全部降为 alias
            others = (
                db.query(BatchName)
                .filter(
                    BatchName.batch_id == batch.id,
                    BatchName.kind == "current",
                    BatchName.name != canonical,
                )
                .all()
            )
            for row in others:
                row.kind = "alias"
        db.commit()
    return created


def register_batch(db: Session, batch: Batch) -> None:
    """新建批次时登记当前号。调用方须保证 batch 已取得 id。

    名称被任何批次（current 或 alias）占用即抛 BatchNumberConflict。
    """
    name = normalize_batch_number(batch.batch_number)
    batch.batch_number = name
    with _rename_lock:
        existing = _get_name_row(db, name)
        if existing is not None:
            if existing.batch_id != batch.id:
                raise BatchNumberConflict(name)
            return
        db.add(BatchName(name=name, kind="current", batch_id=batch.id))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise BatchNumberConflict(name)


def rename_batch(db: Session, batch: Batch, raw_new_name: object) -> str:
    """事务化改名：新号登记为 current，旧号降级为持久化 alias。

    不变量：
      - 新名称若被其它批次以 current 或 alias 形式占用 → BatchNumberConflict，
        原批次与原别名都不动；
      - 新名称若恰好是本批次已有的别名 → 把该别名提升为 current，旧 current 降级
        （同一批次内换号，不会产生二号并存的歧义）；
      - 并发改号由进程锁 + 唯一索引双重保护，不会一号多批，也不会丢别名。
    """
    new_name = normalize_batch_number(raw_new_name)
    old_name = normalize_batch_number(batch.batch_number)
    if new_name == old_name:
        return old_name

    with _rename_lock:
        target = _get_name_row(db, new_name)
        if target is not None and target.batch_id != batch.id:
            # 指向别的批次，无论 current 还是 alias 都拒绝
            raise BatchNumberConflict(new_name)

        old_row = _get_name_row(db, old_name)

        if target is not None:
            # 本批次自己的别名 → 提升为 current
            target.kind = "current"
            if old_row is not None and old_row is not target:
                old_row.kind = "alias"
        else:
            db.add(BatchName(name=new_name, kind="current", batch_id=batch.id))
            if old_row is not None:
                old_row.kind = "alias"
            else:
                # 极端情况：旧号未登记（旧库未回填），补登记为别名
                db.add(BatchName(name=old_name, kind="alias", batch_id=batch.id))

        batch.batch_number = new_name
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise BatchNumberConflict(new_name)

    return new_name


def list_aliases(db: Session, batch_id: int) -> list[str]:
    """返回某批次的全部历史别名（不含当前号），用于排查与展示。"""
    return [
        row.name
        for row in db.query(BatchName)
        .filter(BatchName.batch_id == batch_id, BatchName.kind == "alias")
        .order_by(BatchName.created_at, BatchName.id)
        .all()
    ]
