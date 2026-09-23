"""批次查询契约的回归测试。

覆盖：
- 路由注册顺序：静态批次号入口先于动态 ID 入口，二者互不遮蔽；
- 编码一致性：中文、斜杠、空格在 query 中以 RFC3986 传输，%20 与 '+' 等价；
- Unicode 规范化（NFKC）、首尾空白、大小写的唯一行为，且不合并不同批次号；
- 400（格式非法）/404（不存在）/409（规范化或占用冲突）三种结果严格区分；
- 旧链接 301 兼容跳转并保留查询参数；
- 改号后持久化别名单跳可达、无环、可合法复用、跨批占用被拒；
- 并发改号不会一号多批或丢失原别名；
- 重启（新进程重新建引擎与启动治理）后别名仍可解析；
- 存量数据启动规范化的幂等与冲突拒绝。

每个测试类使用独立的临时 SQLite 数据库，互不干扰。
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# 导入应用前给一个临时默认库（仅用于建表，实际用例各自覆盖数据源）
_TMPDIR = tempfile.mkdtemp(prefix="aqua-contract-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_TMPDIR) / 'default.db'}")
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.main import app  # noqa: E402
from app.database import get_db, get_write_db, make_engine, Base  # noqa: E402
from app.models import Batch, BatchNumberAlias  # noqa: E402
from app.batch_numbers import canonical_url, BatchNumberConflictError  # noqa: E402
from app.batch_service import assign_batch_number, create_batch_with_number  # noqa: E402

client = TestClient(app)


class ContractDBTest(unittest.TestCase):
    """每个测试方法一个全新临时数据库，并覆盖读/写两个 get_db 依赖。"""

    def setUp(self):
        path = str(Path(_TMPDIR) / f"{self.__class__.__name__}-{self._testMethodName}.db")
        if os.path.exists(path):
            os.remove(path)
        self.db_path = path
        url = f"sqlite:///{path}"
        self.read_engine = make_engine(url)
        self.write_engine = make_engine(url, immediate=True)
        Base.metadata.create_all(self.write_engine)
        self.Session = sessionmaker(bind=self.read_engine)
        self.WriteSession = sessionmaker(bind=self.write_engine)
        app.dependency_overrides[get_db] = self._override_get_db
        app.dependency_overrides[get_write_db] = self._override_get_write_db

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_write_db, None)
        self.read_engine.dispose()
        self.write_engine.dispose()

    def _override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def _override_get_write_db(self):
        db = self.WriteSession()
        try:
            yield db
        finally:
            db.close()

    def open_session(self) -> Session:
        return self.Session()


def make_pond(client_: TestClient, name: str) -> int:
    r = client_.post(
        "/api/ponds/",
        json={"name": name, "area": 10.0, "water_depth": 2.0, "species": "草鱼"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_batch(client_: TestClient, number: str, pond_id: int, **overrides) -> dict:
    payload = {
        "batch_number": number,
        "pond_id": pond_id,
        "species": "草鱼",
        "stocking_date": "2026-01-01",
    }
    payload.update(overrides)
    r = client_.post("/api/batches/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


class RouteOrderTest(ContractDBTest):
    def test_by_number_registered_before_dynamic_id_route(self):
        paths = [getattr(route, "path", "") for route in app.routes]
        i_literal = paths.index("/api/batches/by-number")
        i_dynamic = paths.index("/api/batches/{batch_id}/")
        self.assertLess(i_literal, i_dynamic, "批次号入口必须先于 {batch_id} 注册")
        i_tn = paths.index("/api/analysis/trace-by-number")
        i_tid = paths.index("/api/analysis/traceability/{batch_id}/")
        self.assertLess(i_tn, i_tid, "追溯批次号入口必须先于 {batch_id} 注册")

    def test_id_entry_does_not_capture_batch_number_entry(self):
        pond_id = make_pond(client, "塘-路由顺序")
        make_batch(client, "20260923001", pond_id)

        # 纯数字批次号经 by-number 入口命中，绝不会被 /{batch_id} 吞掉
        r = client.get("/api/batches/by-number", params={"batch_number": "20260923001"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["batch_number"], "20260923001")

        # 同串数字在路径上才是 ID
        bid = r.json()["id"]
        r_id = client.get(f"/api/batches/{bid}/")
        self.assertEqual(r_id.status_code, 200)
        self.assertEqual(r_id.json()["id"], bid)

        # 作为"批次号"查不到时必须是 404，而不是误落到 ID 入口
        r_miss = client.get("/api/batches/by-number", params={"batch_number": str(bid)})
        self.assertEqual(r_miss.status_code, 404)
        self.assertEqual(r_miss.json()["code"], "not_found")

        # 非数字走 ID 路径仍是参数校验失败（422），ID 入口保持 int 语义
        r_bad_id = client.get("/api/batches/not-an-id/")
        self.assertEqual(r_bad_id.status_code, 422)


class CanonicalizationTest(ContractDBTest):
    def setUp(self):
        super().setUp()
        self.pond_id = make_pond(client, f"塘-规范化-{self._testMethodName}")

    def test_nfkc_whitespace_case_share_one_canonical_form(self):
        make_batch(client, "abc-001", self.pond_id)  # 存储即规范化为 ABC-001

        ok = client.get("/api/batches/by-number", params={"batch_number": "ABC-001"})
        self.assertEqual(ok.status_code, 200)

        for variant in ("ａｂｃ－００１", "  ABC-001  ", "abc-001", "　ABC-001 "):
            r = client.get(
                "/api/batches/by-number",
                params={"batch_number": variant},
                follow_redirects=False,
            )
            self.assertEqual(r.status_code, 301, variant)
            self.assertEqual(
                r.headers["location"],
                canonical_url("/api/batches/by-number", "ABC-001"),
            )
            followed = client.get(
                "/api/batches/by-number", params={"batch_number": variant}
            )
            self.assertEqual(followed.status_code, 200, variant)

    def test_distinct_numbers_are_never_merged(self):
        # 内部空格不折叠：AB CD 与 ABCD 是两个不同的号
        make_batch(client, "AB CD", self.pond_id)
        make_batch(client, "ABCD", self.pond_id)
        make_batch(client, "ABC-001", self.pond_id)
        make_batch(client, "ABC001", self.pond_id)

        for number in ("AB CD", "ABCD", "ABC-001", "ABC001"):
            r = client.get("/api/batches/by-number", params={"batch_number": number})
            self.assertEqual(r.status_code, 200, number)
            self.assertEqual(r.json()["batch_number"], number)

        # 全角等价物必须视为同一个号：撞号 => 409，绝不静默建第二条
        dup = client.post(
            "/api/batches/",
            json={
                "batch_number": "ａｂｃ－００１",
                "pond_id": self.pond_id,
                "species": "x",
                "stocking_date": "2026-01-02",
            },
        )
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.json()["code"], "conflict")

    def test_stored_form_is_canonical(self):
        b = make_batch(client, "  xyz-九号 ", self.pond_id)
        self.assertEqual(b["batch_number"], "XYZ-九号")


class EncodingTest(ContractDBTest):
    def setUp(self):
        super().setUp()
        self.pond_id = make_pond(client, f"塘-编码-{self._testMethodName}")
        make_batch(client, "2026/批 次-甲", self.pond_id)

    def test_slash_space_chinese_via_query(self):
        raw = "/api/batches/by-number?batch_number=2026%2F%E6%89%B9%20%E6%AC%A1-%E7%94%B2"
        r = client.get(raw)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["batch_number"], "2026/批 次-甲")

    def test_plus_and_percent20_both_decode_as_space(self):
        r_plus = client.get(
            "/api/batches/by-number?batch_number=2026%2F%E6%89%B9+%E6%AC%A1-%E7%94%B2"
        )
        self.assertEqual(r_plus.status_code, 200)
        self.assertEqual(r_plus.json()["batch_number"], "2026/批 次-甲")

    def test_redirect_location_uses_percent20_not_plus(self):
        r = client.get(
            "/api/batches/by-number",
            params={"batch_number": "2026/批 次-甲 "},  # 尾部空格 -> 301
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 301)
        loc = r.headers["location"]
        self.assertIn("%20", loc)
        self.assertNotIn("+", loc)
        self.assertIn("%2F", loc)


class ErrorGradeTest(ContractDBTest):
    def setUp(self):
        super().setUp()
        self.pond_id = make_pond(client, f"塘-错误分级-{self._testMethodName}")

    def test_missing_param_is_400(self):
        r = client.get("/api/batches/by-number")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "invalid_format")

    def test_empty_and_whitespace_are_400(self):
        for value in ("", "   ", "　\t "):
            r = client.get("/api/batches/by-number", params={"batch_number": value})
            self.assertEqual(r.status_code, 400, repr(value))
            self.assertEqual(r.json()["code"], "invalid_format")

    def test_control_character_and_overlength_are_400(self):
        r = client.get("/api/batches/by-number", params={"batch_number": "AB\tC"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "invalid_format")

        r2 = client.get("/api/batches/by-number", params={"batch_number": "X" * 51})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["code"], "invalid_format")

    def test_well_formed_but_missing_is_404(self):
        r = client.get("/api/batches/by-number", params={"batch_number": "NO-SUCH"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_found")

    def test_duplicate_create_is_409(self):
        make_batch(client, "DUP-01", self.pond_id)
        r = client.post(
            "/api/batches/",
            json={
                "batch_number": "dup-01",
                "pond_id": self.pond_id,
                "species": "x",
                "stocking_date": "2026-02-01",
            },
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "conflict")


class LegacyRedirectTest(ContractDBTest):
    def setUp(self):
        super().setUp()
        self.pond_id = make_pond(client, f"塘-旧链-{self._testMethodName}")
        self.b = make_batch(client, "LEGACY-01", self.pond_id)

    def test_legacy_batches_path_migrates_and_keeps_query(self):
        r = client.get(
            "/api/batches/by-number/legacy-01/?src=label&from=%E5%AE%A2%E6%9C%8D",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 301)
        self.assertEqual(
            r.headers["location"],
            "/api/batches/by-number?batch_number=LEGACY-01&src=label&from=%E5%AE%A2%E6%9C%8D",
        )
        followed = client.get("/api/batches/by-number/legacy-01/?src=label")
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.json()["id"], self.b["id"])

    def test_legacy_analysis_path_migrates_and_keeps_query(self):
        r = client.get(
            "/api/analysis/trace-by-number/LEGACY-01/?src=qr",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 301)
        self.assertEqual(
            r.headers["location"],
            "/api/analysis/trace-by-number?batch_number=LEGACY-01&src=qr",
        )
        followed = client.get("/api/analysis/trace-by-number/LEGACY-01/?src=qr")
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.json()["batch"]["batch_number"], "LEGACY-01")

    def test_legacy_unknown_number_still_redirects_then_404(self):
        r = client.get("/api/batches/by-number/GONE-99/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertIn("batch_number=GONE-99", r.headers["location"])
        followed = client.get("/api/batches/by-number/GONE-99/")
        self.assertEqual(followed.status_code, 404)

    def test_trace_by_number_noncanonical_redirects(self):
        r = client.get(
            "/api/analysis/trace-by-number",
            params={"batch_number": " legacy-01 "},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 301)
        self.assertEqual(
            r.headers["location"],
            canonical_url("/api/analysis/trace-by-number", "LEGACY-01"),
        )


class AliasTest(ContractDBTest):
    def setUp(self):
        super().setUp()
        self.pond_id = make_pond(client, f"塘-别名-{self._testMethodName}")
        self.b = make_batch(client, "ALIAS-100", self.pond_id)

    def rename(self, new_number: str):
        return client.put(f"/api/batches/{self.b['id']}/", json={"batch_number": new_number})

    def test_old_link_resolves_via_persistent_alias(self):
        self.assertEqual(self.rename("ALIAS-200").status_code, 200)

        r = client.get(
            "/api/batches/by-number",
            params={"batch_number": "ALIAS-100"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 301)
        self.assertEqual(
            r.headers["location"],
            canonical_url("/api/batches/by-number", "ALIAS-200"),
        )
        followed = client.get(
            "/api/batches/by-number", params={"batch_number": "ALIAS-100"}
        )
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.json()["batch_number"], "ALIAS-200")

    def test_every_previous_number_remains_resolvable_without_chains(self):
        self.rename("ALIAS-200")
        self.rename("ALIAS-300")

        with self.open_session() as db:
            aliases = {a.alias: a.batch_id for a in db.query(BatchNumberAlias).all()}
            alias_keys = set(aliases)
            current_numbers = {x.batch_number for x in db.query(Batch).all()}

        self.assertEqual(aliases.get("ALIAS-100"), self.b["id"])
        self.assertEqual(aliases.get("ALIAS-200"), self.b["id"])
        self.assertNotIn("ALIAS-300", aliases)
        # 别名右值永远是批次（永不指向别名）：别名集合与当前号集合不相交 => 结构性无环
        self.assertTrue(alias_keys.isdisjoint(current_numbers))

        for old in ("ALIAS-100", "ALIAS-200"):
            r = client.get("/api/batches/by-number", params={"batch_number": old})
            self.assertEqual(r.status_code, 200, old)
            self.assertEqual(r.json()["batch_number"], "ALIAS-300")

    def test_rename_back_reuses_own_alias(self):
        self.rename("ALIAS-200")
        r = self.rename("ALIAS-100")  # 改回旧号
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["batch_number"], "ALIAS-100")

        with self.open_session() as db:
            aliases = {a.alias: a.batch_id for a in db.query(BatchNumberAlias).all()}
        self.assertNotIn("ALIAS-100", aliases)
        self.assertEqual(aliases.get("ALIAS-200"), self.b["id"])

        followed = client.get(
            "/api/batches/by-number", params={"batch_number": "ALIAS-200"}
        )
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.json()["batch_number"], "ALIAS-100")

    def test_cross_batch_alias_occupation_is_conflict(self):
        self.rename("ALIAS-200")  # ALIAS-100 成为本批次别名
        other = make_batch(client, "OTHER-01", self.pond_id)

        r = client.put(f"/api/batches/{other['id']}/", json={"batch_number": "ALIAS-100"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "conflict")

        r2 = client.post(
            "/api/batches/",
            json={
                "batch_number": "alias-100",
                "pond_id": self.pond_id,
                "species": "x",
                "stocking_date": "2026-03-01",
            },
        )
        self.assertEqual(r2.status_code, 409)

        # 失败后 other 当前号不变，原别名也未丢失或被改指向
        self.assertEqual(
            client.get(f"/api/batches/{other['id']}/").json()["batch_number"],
            "OTHER-01",
        )
        with self.open_session() as db:
            hit = db.query(BatchNumberAlias).filter(
                BatchNumberAlias.alias == "ALIAS-100"
            ).one()
            self.assertEqual(hit.batch_id, self.b["id"])


class DeleteAliasCleanupTest(ContractDBTest):
    def test_deleting_batch_removes_aliases(self):
        pond_id = make_pond(client, "塘-删除")
        b = make_batch(client, "DEL-01", pond_id)
        self.assertEqual(
            client.put(f"/api/batches/{b['id']}/", json={"batch_number": "DEL-02"}).status_code,
            200,
        )

        r = client.delete(f"/api/batches/{b['id']}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            client.get("/api/batches/by-number", params={"batch_number": "DEL-01"}).status_code,
            404,
        )
        self.assertEqual(
            client.get("/api/batches/by-number", params={"batch_number": "DEL-02"}).status_code,
            404,
        )
        with self.open_session() as db:
            left = db.query(BatchNumberAlias).filter(
                BatchNumberAlias.alias.in_(["DEL-01", "DEL-02"])
            ).count()
        self.assertEqual(left, 0)


class ConcurrentRenameTest(ContractDBTest):
    def test_concurrent_rename_to_same_number_serializes(self):
        pond_id = make_pond(client, "塘-并发改号")
        b1 = make_batch(client, "RACE-A1", pond_id)
        b2 = make_batch(client, "RACE-B1", pond_id)
        target = "RACE-TARGET"
        n = 2
        outcomes: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n)

        def rename(batch_id: int):
            db = self.WriteSession()
            try:
                barrier.wait()
                with db.begin():
                    batch = db.query(Batch).filter(Batch.id == batch_id).first()
                    assign_batch_number(db, batch, target)
                with lock:
                    outcomes.append("ok")
            except BatchNumberConflictError:
                with lock:
                    outcomes.append("conflict")
            finally:
                db.close()

        t1 = threading.Thread(target=rename, args=(b1["id"],))
        t2 = threading.Thread(target=rename, args=(b2["id"],))
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(sorted(outcomes), ["conflict", "ok"])

        with self.open_session() as db:
            winners = db.query(Batch).filter(Batch.batch_number == target).all()
            self.assertEqual(len(winners), 1, "一号多批")
            winner_id = winners[0].id
            loser_id = b2["id"] if winner_id == b1["id"] else b1["id"]
            loser_original = "RACE-B1" if loser_id == b2["id"] else "RACE-A1"
            winner_old = "RACE-A1" if winner_id == b1["id"] else "RACE-B1"
            loser = db.query(Batch).filter(Batch.id == loser_id).one()
            self.assertEqual(loser.batch_number, loser_original)
            alias = db.query(BatchNumberAlias).filter(
                BatchNumberAlias.alias == winner_old
            ).one()
            self.assertEqual(alias.batch_id, winner_id)
            self.assertIsNone(
                db.query(BatchNumberAlias).filter(
                    BatchNumberAlias.alias == loser_original
                ).first()
            )

    def test_concurrent_create_same_number_single_winner(self):
        pond_id = make_pond(client, "塘-并发建号")
        n = 4
        outcomes: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n)

        def create(i: int):
            db = self.WriteSession()
            try:
                barrier.wait()
                with db.begin():
                    create_batch_with_number(
                        db,
                        pond_id=pond_id,
                        batch_number=f"cc-{i % 2}",  # 0/1/0/1 -> 两个号各竞争
                        species="草鱼",
                        stocking_date=datetime.date(2026, 1, 1),
                        estimated_harvest_date=None,
                        actual_harvest_date=None,
                        status="active",
                    )
                with lock:
                    outcomes.append("ok")
            except BatchNumberConflictError:
                with lock:
                    outcomes.append("conflict")
            finally:
                db.close()

        threads = [threading.Thread(target=create, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(outcomes.count("ok"), 2)
        self.assertEqual(outcomes.count("conflict"), 2)
        with self.open_session() as db:
            self.assertEqual(
                db.query(Batch).filter(
                    Batch.batch_number.in_(["CC-0", "CC-1"])
                ).count(),
                2,
            )

    def test_read_not_blocked_during_write_transaction(self):
        pond_id = make_pond(client, "塘-读写并发")
        make_batch(client, "RW-01", pond_id)
        entered = threading.Event()

        def slow_write():
            db = self.WriteSession()
            try:
                with db.begin():
                    batch = db.query(Batch).filter(Batch.batch_number == "RW-01").first()
                    assign_batch_number(db, batch, "RW-02")
                    entered.set()
                    time.sleep(0.6)  # 持写锁期间，读仍应可用
            finally:
                db.close()

        t = threading.Thread(target=slow_write)
        t.start()
        self.assertTrue(entered.wait(2))
        # 写事务未提交：读不阻塞（WAL），读到的是提交前的旧号
        r = client.get("/api/batches/by-number", params={"batch_number": "RW-01"})
        self.assertEqual(r.status_code, 200)
        t.join()
        # 提交后：旧号经别名 301，新号 200
        r_new = client.get("/api/batches/by-number", params={"batch_number": "RW-02"})
        self.assertEqual(r_new.status_code, 200)
        r_old = client.get(
            "/api/batches/by-number", params={"batch_number": "RW-01"},
            follow_redirects=False,
        )
        self.assertEqual(r_old.status_code, 301)

    def test_concurrent_http_puts_same_target_single_winner(self):
        """经完整 HTTP 栈（独立 TestClient/线程/连接）并发改同一目标号。"""
        pond_id = make_pond(client, "塘-HTTP并发")
        b1 = make_batch(client, "HTTP-A", pond_id)
        b2 = make_batch(client, "HTTP-B", pond_id)
        target = "HTTP-TARGET"
        n = 2
        statuses: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n)

        def put(batch_id: int):
            local = TestClient(app)
            barrier.wait()
            r = local.put(f"/api/batches/{batch_id}/", json={"batch_number": target})
            with lock:
                statuses.append(r.status_code)

        t1 = threading.Thread(target=put, args=(b1["id"],))
        t2 = threading.Thread(target=put, args=(b2["id"],))
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(sorted(statuses), [200, 409])
        with self.open_session() as db:
            winners = db.query(Batch).filter(Batch.batch_number == target).all()
            self.assertEqual(len(winners), 1)
            # 成功者的旧别名保留，失败者的号不丢
            self.assertEqual(
                db.query(BatchNumberAlias).filter(
                    BatchNumberAlias.alias.in_(["HTTP-A", "HTTP-B"])
                ).count(),
                1,
            )


class RestartPersistenceTest(ContractDBTest):
    """用全新子进程重新导入应用，模拟服务重启后的解析。"""

    def test_alias_survives_restart(self):
        pond_id = make_pond(client, "塘-重启")
        b = make_batch(client, "RESTART-OLD", pond_id)
        self.assertEqual(
            client.put(
                f"/api/batches/{b['id']}/", json={"batch_number": "RESTART-NEW"}
            ).status_code,
            200,
        )

        probe = r"""
import json, sys
sys.path.insert(0, %r)
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    old = c.get('/api/batches/by-number', params={'batch_number': 'RESTART-OLD'}, follow_redirects=False)
    cur = c.get('/api/batches/by-number', params={'batch_number': 'RESTART-NEW'})
    legacy = c.get('/api/batches/by-number/RESTART-OLD/', follow_redirects=False)
print(json.dumps({
    'old_status': old.status_code,
    'old_location': old.headers.get('location'),
    'cur_status': cur.status_code,
    'cur_number': cur.json().get('batch_number') if cur.status_code == 200 else None,
    'legacy_status': legacy.status_code,
    'legacy_location': legacy.headers.get('location'),
}))
""" % str(BACKEND)

        env = dict(os.environ, DATABASE_URL=f"sqlite:///{self.db_path}")
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
        data = json.loads(line)
        self.assertEqual(data["old_status"], 301)
        self.assertEqual(
            data["old_location"],
            canonical_url("/api/batches/by-number", "RESTART-NEW"),
        )
        self.assertEqual(data["cur_status"], 200)
        self.assertEqual(data["cur_number"], "RESTART-NEW")
        self.assertEqual(data["legacy_status"], 301)
        self.assertIn("batch_number=RESTART-NEW", data["legacy_location"])


class StartupNormalizationTest(unittest.TestCase):
    def test_legacy_rows_normalized_idempotently_and_conflict_rejected(self):
        from app.startup import normalize_existing_batch_numbers
        from sqlalchemy import text

        # 场景一：幂等收敛
        path = str(Path(_TMPDIR) / "startup.db")
        if os.path.exists(path):
            os.remove(path)
        eng = make_engine(f"sqlite:///{path}")
        Base.metadata.create_all(eng)
        with eng.begin() as conn:
            conn.execute(
                text("INSERT INTO ponds (name, area, water_depth, species, status) "
                     "VALUES ('s1', 1, 1, 'x', 'active')")
            )
            conn.execute(
                text("INSERT INTO batches (batch_number, pond_id, species, stocking_date, status) "
                     "VALUES ('legacy-x', 1, 'x', '2026-01-01', 'active')")
            )
        normalize_existing_batch_numbers(eng)
        with Session(eng) as s:
            self.assertEqual(s.query(Batch).one().batch_number, "LEGACY-X")
        normalize_existing_batch_numbers(eng)
        with Session(eng) as s:
            self.assertEqual(s.query(Batch).one().batch_number, "LEGACY-X")

        # 场景二：规范化后撞号 => 拒绝启动而不是静默合并
        path2 = str(Path(_TMPDIR) / "startup_conflict.db")
        if os.path.exists(path2):
            os.remove(path2)
        eng2 = make_engine(f"sqlite:///{path2}")
        Base.metadata.create_all(eng2)
        with eng2.begin() as conn:
            conn.execute(
                text("INSERT INTO ponds (name, area, water_depth, species, status) "
                     "VALUES ('s2', 1, 1, 'x', 'active')")
            )
            conn.execute(
                text("INSERT INTO batches (batch_number, pond_id, species, stocking_date, status) "
                     "VALUES ('abc', 1, 'x', '2026-01-01', 'active'),"
                     "        ('ABC', 1, 'x', '2026-01-02', 'active')")
            )
        with self.assertRaises(RuntimeError):
            normalize_existing_batch_numbers(eng2)
        with Session(eng2) as s:
            self.assertEqual({b.batch_number for b in s.query(Batch).all()}, {"abc", "ABC"})


if __name__ == "__main__":
    unittest.main()
