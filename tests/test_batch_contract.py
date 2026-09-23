"""批次查询契约回归测试。

覆盖：
  - 路由顺序：静态批次号入口先于动态 ID 入口；ID 入口使用 int 转换器
  - 数字样式批次号不被 ID 动态路由遮蔽
  - 编码（斜杠/空格/中文/%20）、NFC 归一化、首尾空白、大小写敏感
  - 404（找不到）/ 422（格式非法）/ 409（规范化冲突）三态分离
  - 旧路径链接 301 兼容并保留查询参数
  - 改名后持久化别名继续定位、别名不会成环；原名丢失防护
  - 并发改号不产生一号多批
  - 子进程重启后的解析（独立文件型 SQLite）
"""

import os
import subprocess
import sys
import threading
import time
import tempfile
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# 每个测试模块共用一个临时数据库
_TMP_DB = Path(tempfile.mkdtemp(prefix="batch-contract-") ) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Batch, BatchName  # noqa: E402
from app.services import batch_numbers as bn  # noqa: E402


def _make_pond(client, name: str) -> int:
    r = client.post(
        "/api/ponds/",
        json={"name": name, "area": 5.0, "water_depth": 1.5},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_batch(client, pond_id: int, number: str, **kw) -> dict:
    payload = {
        "batch_number": number,
        "pond_id": pond_id,
        "species": "草鱼",
        "stocking_date": "2024-04-01",
    }
    payload.update(kw)
    r = client.post("/api/batches/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


class RouteOrderTest(unittest.TestCase):
    """路由表层面的遮蔽防护契约。"""

    def test_static_number_route_registered_before_dynamic_id_routes(self):
        batch_routes = [
            (r.path, sorted(r.methods))
            for r in app.routes
            if getattr(r, "path", "").startswith("/api/batches")
        ]
        paths = [p for p, _ in batch_routes]
        static_idx = paths.index("/api/batches/by-number")
        legacy_idx = paths.index("/api/batches/by-number/{legacy:path}")
        id_indices = [i for i, p in enumerate(paths) if "{batch_id" in p]
        # 静态与旧路径兼容路由都必须在任何动态 ID 路由之前
        for i in id_indices:
            self.assertLess(static_idx, i)
            self.assertLess(legacy_idx, i)
        # ID 入口必须带 int 转换器，纯数字以外的路径段不可能命中
        for p in paths:
            if "{batch_id" in p:
                self.assertIn("{batch_id:int}", p)

    def test_numeric_like_batch_number_not_shadowed_by_id_route(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "route-order-pond")
            b = _make_batch(c, pond, "20240923001")
            # 按数字样式批次号查询必须命中批次，而不是被当作 ID（ID 不存在→404）
            r = c.get("/api/batches/by-number", params={"n": "20240923001"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["id"], b["id"])
            self.assertEqual(r.json()["batch_number"], "20240923001")

    def test_unknown_id_is_404_not_confused_with_number(self):
        with TestClient(app) as c:
            r = c.get("/api/batches/99999999/")
            self.assertEqual(r.status_code, 404)
            r = c.get("/api/batches/abc/")
            # 非数字不再匹配 ID 路由，也没有该字面路由
            self.assertEqual(r.status_code, 404)


class EncodingAndNormalizationTest(unittest.TestCase):
    def test_slash_space_chinese_in_query(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "enc-pond")
            _make_batch(c, pond, "2024/A 批次01")
            # 原始空格、斜杠、中文经 query 传入
            r = c.get(
                "/api/batches/by-number",
                params={"n": "2024/A 批次01"},
                follow_redirects=False,
            )
            self.assertEqual(r.status_code, 200)

    def test_encoded_slash_and_space_have_unique_behavior(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "enc-pond-2")
            _make_batch(c, pond, "B/2024 001")
            # 直接请求已编码 URL：%2F 与 %20 必须被唯一解码
            r = c.get("/api/batches/by-number?n=B%2F2024%20001", follow_redirects=False)
            self.assertEqual(r.status_code, 200)

    def test_trim_redirects_to_canonical(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "trim-pond")
            _make_batch(c, pond, "TRIM-01")
            for raw in ("  TRIM-01", "TRIM-01\t", "　TRIM-01　"):
                r = c.get(
                    "/api/batches/by-number",
                    params={"n": raw},
                    follow_redirects=False,
                )
                self.assertEqual(r.status_code, 301, raw)
                loc = r.headers["location"]
                self.assertIn("n=TRIM-01", loc)
                # 跟随 301 拿到数据（TestClient 基于 http://testserver，可直接传路径）
                r2 = c.get(loc, follow_redirects=False)
                self.assertEqual(r2.status_code, 200)

    def test_nfc_normalization_redirect(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "nfc-pond")
            nfc = unicodedata.normalize("NFC", "Ångström-01")
            _make_batch(c, pond, nfc)
            nfd = unicodedata.normalize("NFD", nfc)
            self.assertNotEqual(nfc, nfd)
            r = c.get("/api/batches/by-number", params={"n": nfd}, follow_redirects=False)
            self.assertEqual(r.status_code, 301)
            from urllib.parse import quote
            self.assertIn(quote(unicodedata.normalize("NFC", "Ångström-01"), safe=""),
                          r.headers["location"])
            r = c.get("/api/batches/by-number", params={"n": nfd})
            self.assertEqual(r.status_code, 200)

    def test_case_sensitive_distinct_batches_never_merged(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "case-pond")
            upper = _make_batch(c, pond, "CASE-X")
            lower = _make_batch(c, pond, "case-x", species="鲫鱼")
            self.assertNotEqual(upper["id"], lower["id"])
            ru = c.get("/api/batches/by-number", params={"n": "CASE-X"})
            rl = c.get("/api/batches/by-number", params={"n": "case-x"})
            self.assertEqual(ru.json()["id"], upper["id"])
            self.assertEqual(rl.json()["id"], lower["id"])
            # 折叠大小写但都不精确存在 → 409 冲突，而非 404 或随便命中
            db = SessionLocal()
            try:
                # 确保 "Case-X" 不存在，但折叠后命中
                pass
            finally:
                db.close()
            r = c.get("/api/batches/by-number", params={"n": "Case-X"}, follow_redirects=False)
            self.assertEqual(r.status_code, 409)


class ThreeStatesTest(unittest.TestCase):
    def test_not_found_vs_invalid_vs_conflict(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "states-pond")
            _make_batch(c, pond, "STATE-01")

            r = c.get("/api/batches/by-number", params={"n": "STATE-02"}, follow_redirects=False)
            self.assertEqual(r.status_code, 404)

            for bad in ("", "   ", "AB\x00", "x" * 51):
                r = c.get("/api/batches/by-number", params={"n": bad}, follow_redirects=False)
                self.assertEqual(r.status_code, 422, bad)

            r = c.get("/api/batches/by-number", params={"n": "state-01"}, follow_redirects=False)
            self.assertEqual(r.status_code, 409)

    def test_missing_n_param_is_422(self):
        with TestClient(app) as c:
            r = c.get("/api/batches/by-number")
            self.assertEqual(r.status_code, 422)


class LegacyLinkCompatTest(unittest.TestCase):
    def test_legacy_path_redirects_and_preserves_query(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "legacy-pond")
            _make_batch(c, pond, "LEG/01")
            r = c.get(
                "/api/batches/by-number/LEG%2F01/?src=qrcode&campaign=x",
                follow_redirects=False,
            )
            self.assertEqual(r.status_code, 301)
            loc = r.headers["location"]
            self.assertTrue(loc.startswith("/api/batches/by-number?"))
            self.assertIn("src=qrcode", loc)
            self.assertIn("campaign=x", loc)
            self.assertIn("n=LEG%2F01", loc)
            # 端到端跟随
            r = c.get("/api/batches/by-number/LEG%2F01/?src=qrcode")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["batch_number"], "LEG/01")

    def test_legacy_trace_path_redirects(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "legacy-trace-pond")
            _make_batch(c, pond, "LEG-TR-01")
            r = c.get(
                "/api/analysis/trace-by-number/LEG-TR-01?from=cs",
                follow_redirects=False,
            )
            self.assertEqual(r.status_code, 301)
            loc = r.headers["location"]
            self.assertIn("n=LEG-TR-01", loc)
            self.assertIn("from=cs", loc)
            r = c.get("/api/analysis/trace-by-number/LEG-TR-01?from=cs")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["batch"]["batch_number"], "LEG-TR-01")


class AliasAndRenameTest(unittest.TestCase):
    def test_rename_keeps_persistent_alias_and_redirects(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "rename-pond")
            b = _make_batch(c, pond, "OLD-01")
            r = c.put(f"/api/batches/{b['id']}/", json={"batch_number": "NEW-01"})
            self.assertEqual(r.status_code, 200)

            # 旧链接持久命中并 301 到新号
            r = c.get("/api/batches/by-number", params={"n": "OLD-01"}, follow_redirects=False)
            self.assertEqual(r.status_code, 301)
            self.assertIn("n=NEW-01", r.headers["location"])
            r = c.get("/api/batches/by-number", params={"n": "OLD-01"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["id"], b["id"])

            # 别名记录落库
            db = SessionLocal()
            try:
                names = {
                    row.name: row.kind
                    for row in db.query(BatchName).filter(BatchName.batch_id == b["id"]).all()
                }
                self.assertEqual(names.get("NEW-01"), "current")
                self.assertEqual(names.get("OLD-01"), "alias")
            finally:
                db.close()

    def test_rename_conflict_leaves_everything_intact(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "rename-conflict-pond")
            a = _make_batch(c, pond, "TAKEN-01")
            b = _make_batch(c, pond, "FREE-01")
            r = c.put(f"/api/batches/{b['id']}/", json={"batch_number": "TAKEN-01"})
            self.assertEqual(r.status_code, 409)
            # 原号仍可解析且指向原批次；被占用号仍属于 A
            self.assertEqual(
                c.get("/api/batches/by-number", params={"n": "FREE-01"}).json()["id"],
                b["id"],
            )
            self.assertEqual(
                c.get("/api/batches/by-number", params={"n": "TAKEN-01"}).json()["id"],
                a["id"],
            )
            db = SessionLocal()
            try:
                # 没有产生多余别名
                aliases = (
                    db.query(BatchName)
                    .filter(BatchName.batch_id == b["id"], BatchName.kind == "alias")
                    .count()
                )
                self.assertEqual(aliases, 0)
            finally:
                db.close()

    def test_repeated_renames_all_aliases_resolve_no_cycle_possible(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "multi-rename-pond")
            b = _make_batch(c, pond, "M-01")
            for n in ("M-02", "M-03", "M-04"):
                r = c.put(f"/api/batches/{b['id']}/", json={"batch_number": n})
                self.assertEqual(r.status_code, 200)
            for n in ("M-01", "M-02", "M-03", "M-04"):
                r = c.get(f"/api/batches/by-number?n={n}")
                self.assertEqual(r.status_code, 200, n)
                self.assertEqual(r.json()["id"], b["id"], n)
            # 当前号
            self.assertEqual(
                c.get("/api/batches/by-number", params={"n": "M-04"}).json()["batch_number"],
                "M-04",
            )
            # 改回早期别名：别名提升，旧当前号降级
            r = c.put(f"/api/batches/{b['id']}/", json={"batch_number": "M-01"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(
                c.get("/api/batches/by-number", params={"n": "M-04"}).json()["batch_number"],
                "M-01",
            )

    def test_alias_name_cannot_be_taken_by_another_batch(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "alias-reserve-pond")
            b = _make_batch(c, pond, "RESERVE-OLD")
            self.assertEqual(
                c.put(f"/api/batches/{b['id']}/", json={"batch_number": "RESERVE-NEW"}).status_code,
                200,
            )
            other = _make_batch(c, pond, "RESERVE-OTHER")
            # 别的批次不能创建为已被别名占用的号
            r = c.post(
                "/api/batches/",
                json={
                    "batch_number": "RESERVE-OLD",
                    "pond_id": pond,
                    "species": "草鱼",
                    "stocking_date": "2024-04-01",
                },
            )
            self.assertEqual(r.status_code, 409)
            # 也不能改名为该别名
            r = c.put(f"/api/batches/{other['id']}/", json={"batch_number": "RESERVE-OLD"})
            self.assertEqual(r.status_code, 409)
            # 旧链接仍然唯一定位原批次
            self.assertEqual(
                c.get("/api/batches/by-number", params={"n": "RESERVE-OLD"}).json()["id"],
                b["id"],
            )

    def test_concurrent_renames_no_dual_ownership_no_lost_alias(self):
        with TestClient(app) as c:
            pond = _make_pond(c, "concurrent-pond")
            b1 = _make_batch(c, pond, "C-ORIG-1")
            b2 = _make_batch(c, pond, "C-ORIG-2")
            errors = []

            def rename(bid, new_no):
                try:
                    rr = c.put(f"/api/batches/{bid}/", json={"batch_number": new_no})
                    if rr.status_code not in (200, 409):
                        errors.append((bid, new_no, rr.status_code))
                except Exception as e:  # pragma: no cover
                    errors.append((bid, new_no, repr(e)))

            # 两个批次同时争抢同一新号
            t1 = threading.Thread(target=rename, args=(b1["id"], "C-NEW-SHARED"))
            t2 = threading.Thread(target=rename, args=(b2["id"], "C-NEW-SHARED"))
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(errors, [])

            db = SessionLocal()
            try:
                owners = db.query(BatchName).filter(BatchName.name == "C-NEW-SHARED").all()
                self.assertEqual(len(owners), 1, "新号只能指向一个批次")
                winner = owners[0].batch_id

                # 两个原号都必须仍可解析（current 或 alias），无丢失
                for orig, bid in (("C-ORIG-1", b1["id"]), ("C-ORIG-2", b2["id"])):
                    row = db.query(BatchName).filter(BatchName.name == orig).one()
                    self.assertEqual(row.batch_id, bid)
                    if bid == winner:
                        self.assertEqual(row.kind, "alias")
                    else:
                        self.assertEqual(row.kind, "current")
            finally:
                db.close()


class BackfillAndRestartTest(unittest.TestCase):
    def test_backfill_idempotent(self):
        db = SessionLocal()
        try:
            before = db.query(BatchName).count()
            self.assertEqual(bn.backfill_names(db), 0)  # 已回填
            self.assertEqual(db.query(BatchName).count(), before)
        finally:
            db.close()

    def test_resolution_after_process_restart(self):
        # 用全新临时库：预先只写 batches 表（模拟旧库），再用子进程启动应用，
        # 通过 HTTP 验证启动回填后旧批次号可解析
        db_dir = Path(tempfile.mkdtemp(prefix="batch-restart-"))
        db_path = db_dir / "restart.db"

        from app.database import Base, engine as _unused  # noqa
        # 在临时 URL 上手工建表并写一条旧数据
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        eng = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        # 只建 ponds/batches（不含 batch_names），模拟升级前结构
        PondModel = Batch.__table__
        # 建立全部表但删除 batch_names，模拟旧库
        Base.metadata.create_all(bind=eng)
        with eng.connect() as conn:
            conn.exec_driver_sql("DROP TABLE batch_names")
            conn.commit()
        Sess = sessionmaker(bind=eng)
        s = Sess()
        s.connection().exec_driver_sql(
            "INSERT INTO ponds (name, area, water_depth, status, created_at, updated_at) "
            "VALUES ('restart-pond', 3, 1.2, 'active', '2024-01-01 00:00:00', '2024-01-01 00:00:00')"
        )
        s.connection().exec_driver_sql(
            "INSERT INTO batches (batch_number, pond_id, species, stocking_date, status, created_at, updated_at) "
            "VALUES ('RESTART/01 ', 1, '草鱼', '2024-04-01', 'active', '2024-04-01 00:00:00', '2024-04-01 00:00:00')"
        )
        s.commit()
        s.close()

        script = "\n".join([
            "import os, sys",
            "os.environ['DATABASE_URL']='sqlite:///" + str(db_path) + "'",
            "sys.path.insert(0, " + repr(str(BACKEND)) + ")",
            "from fastapi.testclient import TestClient",
            "from app.main import app",
            # with 触发 startup 事件（执行旧库回填），随后请求验证解析
            "with TestClient(app) as c:",
            "    r = c.get('/api/batches/by-number?n=RESTART%2F01')",
            "    assert r.status_code == 200, r.text",
            "    assert r.json()['batch_number']=='RESTART/01', r.text",
            "    r2 = c.get('/api/analysis/trace-by-number?n=RESTART%2F01')",
            "    assert r2.status_code == 200, r2.text",
            "print('RESTART-RESOLVE-OK')",
        ])
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertIn("RESTART-RESOLVE-OK", proc.stdout, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
