"""启动时数据治理：把存量批次号收敛到契约规定的唯一规范形态。

- 幂等：已经规范的数据不产生任何变更。
- 绝不静默合并：若两条存量批次规范化后撞号，启动失败并列出冲突，
  交由人工处理（数据库层唯一约束也会兜底，但这里给出可操作的错误）。
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from .batch_numbers import canonicalize_batch_number
from .models import Batch, BatchNumberAlias

logger = logging.getLogger(__name__)


def normalize_existing_batch_numbers(engine: Engine) -> None:
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        batches = db.query(Batch).all()

        # 第一遍：检测规范化后的占用冲突（含别名）
        seen: dict[str, int] = {}
        conflicts: list[tuple[str, int, int]] = []
        for batch in batches:
            canonical = canonicalize_batch_number(batch.batch_number)
            if canonical in seen and seen[canonical] != batch.id:
                conflicts.append((canonical, seen[canonical], batch.id))
            else:
                seen[canonical] = batch.id
        if conflicts:
            details = ", ".join(
                f"规范号 {num!r} 同时被批次 {a} 与 {b} 占用"
                for num, a, b in conflicts
            )
            raise RuntimeError(
                "存量批次号规范化后存在冲突，拒绝启动以避免错误合并: " + details
            )

        # 第二遍：规范化当前号；被改号的旧值若属于其他批次的历史别名
        # （升级场景），保持别名不动会与新当前号主键冲突——此时同样报错。
        for batch in batches:
            canonical = canonicalize_batch_number(batch.batch_number)
            if canonical == batch.batch_number:
                continue
            blocking_alias = db.query(BatchNumberAlias).filter(
                BatchNumberAlias.alias == canonical,
                BatchNumberAlias.batch_id != batch.id,
            ).first()
            if blocking_alias is not None:
                raise RuntimeError(
                    f"批次 {batch.id} 规范化后的号 {canonical!r} "
                    f"与批次 {blocking_alias.batch_id} 的历史别名冲突，拒绝启动"
                )
            batch.batch_number = canonical
            logger.info("批次 %s 的批次号已规范化", batch.id)

        db.commit()
