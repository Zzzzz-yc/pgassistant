"""API 层的 SQLite 线程安全回归（M0 缺陷）。

现象：浏览器并发首屏（`/api/taxonomy` + `/api/resources` + `/api/plans` 同时打）时 500，
日志 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in
that same thread`。

根因：`api/deps.py::get_conn` 是**同步生成器依赖**，FastAPI 可能在线程池的 A 线程执行
`__enter__`（建连接）、B 线程执行 endpoint、C 线程执行 `__exit__`（close）。而
`db/connection.py::connect` 用的是 `sqlite3.connect` 默认 `check_same_thread=True`。
"一请求一连接、请求内串行"这个前提不变，所以放开同线程限制是安全且最小的修法。

两条回归：
1. `test_connection_created_in_one_thread_usable_in_another`：直接钉根因（A 建、B 用）；
2. `test_taxonomy_concurrent_requests_all_200`：8 线程 × 6 次真实 HTTP（48 请求）全 200。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

THREADS = 8
ROUNDS = 6
TOTAL = THREADS * ROUNDS  # 48


def test_connection_created_in_one_thread_usable_in_another(db_path):
    """A 线程建连接、B 线程查询必须成功（不看 HTTP，直接钉住 sqlite3 的行为）。"""
    from app.db.connection import connect

    conn = connect(db_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            count = pool.submit(
                lambda: conn.execute("SELECT COUNT(*) AS n FROM resources").fetchone()["n"]
            ).result()
    finally:
        conn.close()
    assert count >= 1


def test_taxonomy_concurrent_requests_all_200(client, token):
    """8 线程 × 6 次 `GET /api/taxonomy`：48/48 必须 200。"""

    def hit(_: int) -> tuple[int, str]:
        resp = client.get("/api/taxonomy", headers={"X-App-Token": token})
        return resp.status_code, resp.text[:200]

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        results = list(pool.map(hit, range(TOTAL)))

    failures = [(code, body) for code, body in results if code != 200]
    assert not failures, f"{len(failures)}/{TOTAL} 非 200，首条：{failures[0]}"
    assert len(results) == TOTAL
