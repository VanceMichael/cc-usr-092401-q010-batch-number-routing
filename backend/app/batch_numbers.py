"""批次号契约：编码、Unicode 规范化、首尾空白与大小写的唯一行为。

规则（适用于所有批次号入口，服务端为唯一权威）：

1. 百分号解码与路径安全字符由 ASGI/路由层处理，应用层永远只拿已解码的
   字符串；批次号正式入口使用 query 参数，彻底避开路径段的编码差异。
2. 到达应用层后统一执行：NFKC 规范化 -> 去除全部首尾空白（strip）->
   转为大写。该结果是批次号的唯一规范形态，存储与查询都以它为准。
3. 规范化后仍为空、含控制字符或超长（>50 码点）即格式非法（400），
   与"查不到"（404）和"规范化冲突"（409）严格区分。
4. 任何两个不同的原始批次号若规范化结果相同，就必须判定为同一个号；
   服务端绝不允许两条批次记录共用同一规范号（唯一约束 + 冲突检测），
   因此不会把两个原本不同的批次号错误合并。
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping
from urllib.parse import quote

# 与 models.Batch.batch_number 的长度一致
MAX_BATCH_NUMBER_LENGTH = 50


class BatchNumberError(ValueError):
    """批次号契约错误基类。

    code 为稳定的机器可读错误码：
    - invalid_format   格式非法（400）
    - not_found        规范号不存在（404）
    - conflict         规范化/占用冲突（409）
    """

    status_code: int = 400
    code: str = "invalid_format"

    def __init__(self, message: str, *, code: str | None = None, detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})


class InvalidBatchNumberError(BatchNumberError):
    status_code = 400
    code = "invalid_format"


class BatchNumberNotFoundError(BatchNumberError):
    status_code = 404
    code = "not_found"


class BatchNumberConflictError(BatchNumberError):
    status_code = 409
    code = "conflict"


def canonicalize_batch_number(raw: str) -> str:
    """把任意来源的批次号转为唯一规范形态。

    顺序固定为 NFKC -> strip -> upper，任何入口都不得绕过这一步。
    不对内部空白做折叠，因此 "AB CD" 与 "ABCD" 仍是两个不同的号。
    """
    if not isinstance(raw, str):
        raise InvalidBatchNumberError("批次号必须是字符串")
    return unicodedata.normalize("NFKC", raw).strip().upper()


def validate_canonical(value: str) -> str:
    """校验已经规范化的批次号，非法则抛 InvalidBatchNumberError。"""
    if not value:
        raise InvalidBatchNumberError("批次号不能为空")
    if len(value) > MAX_BATCH_NUMBER_LENGTH:
        raise InvalidBatchNumberError(
            f"批次号长度不能超过 {MAX_BATCH_NUMBER_LENGTH} 个字符",
            detail={"max_length": MAX_BATCH_NUMBER_LENGTH},
        )
    for ch in value:
        if unicodedata.category(ch) in ("Cc", "Cf"):
            raise InvalidBatchNumberError(
                "批次号不能包含控制字符", detail={"character": repr(ch)}
            )
    return value


def normalize_batch_number(raw: str) -> str:
    """规范化 + 校验，返回可存储/查询的规范批次号。"""
    return validate_canonical(canonicalize_batch_number(raw))


def has_variation(raw: str) -> bool:
    """输入与规范形态是否不同（用于决定要不要 301 规范化跳转）。"""
    canonical = canonicalize_batch_number(raw)
    return raw != canonical


def canonical_url(path: str, batch_number: str, query: Mapping[str, str] | None = None) -> str:
    """构造批次号入口的唯一规范地址。

    批次号统一放 query（?batch_number=），用与 JS encodeURIComponent 一致的
    RFC3986 百分号编码（空格固定 %20、斜杠 %2F、中文 UTF-8 字节序列），
    浏览器地址栏、前端代码与服务端 301 产生的写法逐字符一致，斜杠不会被
    误切成路径段。原查询参数（如追溯链接的来源参数）全部保留。
    """
    parts = [f"batch_number={quote(batch_number, safe='')}"]
    for key, value in (query or {}).items():
        if key == "batch_number":
            continue
        parts.append(f"{quote(str(key), safe='')}={quote(str(value), safe='')}")
    return f"{path}?{'&'.join(parts)}"
