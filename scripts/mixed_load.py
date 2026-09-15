# -*- coding: utf-8 -*-
"""混合负载尾延迟实验（r25/P1-6）。

以真实 HTTP 客户端并发驱动预约/取消/候补/审批/改期/读列表的混合负载，
输出 p50/p95/p99 延迟、状态码分布（含 503）与 WAL 字节增长，报告落盘
docs/evidence/load/。样本与环境为本机回环，结论按「实验证据」口径解读。
"""
from __future__ import annotations
import argparse, contextlib, http.client, json, os, pathlib, random, sqlite3, statistics, subprocess, sys, threading, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 压测目标固定为本机回环服务；--port 仅作为 HTTPConnection 的端口参数，不参与任何 URL 拼接
HOST = "127.0.0.1"

def now(): return time.monotonic()

def req(port, method, path, body=None, cookie=None, csrf=None, token=None):
    headers = {"Content-Type": "application/json"}
    if cookie: headers["Cookie"] = cookie
    if csrf: headers["X-CSRF-Token"] = csrf
    if token: headers["X-API-Token"] = token
    payload = json.dumps(body).encode() if body is not None else None
    conn = http.client.HTTPConnection(HOST, int(port), timeout=10)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        content = response.read()
        try: parsed = json.loads(content)
        except ValueError: parsed = {}
        return response.status, parsed, response.getheader("Set-Cookie")
    finally:
        conn.close()

def pct(samples, p):
    if not samples: return None
    s = sorted(samples); k = min(len(s) - 1, max(0, round((len(s) - 1) * p / 100)))
    return s[k]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", default=str(ROOT / "build/lab-booking.exe"))
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--duration", type=int, default=30, help="施压秒数")
    ap.add_argument("--concurrency", type=int, default=8, help="写负载线程数")
    ap.add_argument("--password", default=os.environ.get("LAB_TEST_PASSWORD", "MixedLoad-2026"))
    ap.add_argument("--out", default=str(ROOT / "docs/evidence/load"))
    args = ap.parse_args()
    if len(args.password) < 8: ap.error("口令至少 8 位")
    run_dir = pathlib.Path("artifacts/load-run"); run_dir.mkdir(parents=True, exist_ok=True)
    for old in run_dir.glob("*.db*"): old.unlink()
    db = run_dir / "load.db"
    env = dict(os.environ); env["LAB_SEED_PASSWORD"] = args.password
    r = subprocess.run([args.exe, "--db", str(db), "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    log = open(run_dir / "server.log", "ab")
    proc = subprocess.Popen([args.exe, "--db", str(db), "--web", str(ROOT / "web"), "--port", str(args.port),
                             "--checkin-window", "60", "--sweep-interval", "1", "--rate-burst", "100000",
                             "--hold-window", "120"],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    results = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": args.duration,
               "concurrency": args.concurrency, "samples": [], "wal_bytes": []}
    try:
        deadline = now() + 15
        while now() < deadline:
            st, body, _ = req(args.port, "GET", "/api/health")
            if st == 200: break
            time.sleep(0.2)
        else: raise AssertionError("server not ready")
        pw = args.password
        def login(name):
            st, b, ck = req(args.port, "POST", "/api/login", {"username": name, "password": pw})
            assert st == 200, (name, st, b)
            return ck.split(";", 1)[0], b["data"]["csrf_token"]
        users = [f"user{i:02d}" for i in range(1, 7)]
        sessions = {u: login(u) for u in users}
        adm_ck, adm_csrf = login("admin")
        # 布景：两个实验室 × 未来 14 天场次，容量 2，资源若干
        labs = []
        for k in range(2):
            st, b, _ = req(args.port, "POST", "/api/admin/labs", {"name": f"负载实验室{k}{uuid.uuid4().hex[:4]}", "location": "x", "description": "load"},
                           cookie=adm_ck, csrf=adm_csrf)
            labs.append(b["data"]["lab_id"])
        base = (int(time.time()) + 86400 + 28800) // 86400 * 86400 - 28800 + 9 * 3600
        day = time.strftime("%Y-%m-%d", time.localtime(base))
        day_end = time.strftime("%Y-%m-%d", time.localtime(base + 13 * 86400))
        for lab in labs:
            st, b, _ = req(args.port, "POST", "/api/admin/slots/publish",
                           {"lab_id": str(lab), "start_date": day, "end_date": day_end, "capacity": "2"},
                           cookie=adm_ck, csrf=adm_csrf)
            assert st == 200, b
        with contextlib.closing(sqlite3.connect(db, timeout=5)) as conn:
            slots = [row[0] for row in conn.execute("SELECT id FROM slots WHERE enabled=1 AND start_at>? ORDER BY id", (int(time.time()),))]
        print(f"布景完成：{len(labs)} 实验室，{len(slots)} 场次，{len(users)} 用户，施压 {args.duration}s")
        stop = threading.Event()
        lock = threading.Lock()
        mine = {}  # username -> list[reservation_id]

        def record(op, st, t0):
            with lock:
                results["samples"].append({"op": op, "status": st, "ms": round((now() - t0) * 1000, 2)})

        def wal_probe():
            wal = pathlib.Path(str(db) + "-wal")
            while not stop.is_set():
                results["wal_bytes"].append({"t": round(now(), 1), "bytes": wal.stat().st_size if wal.exists() else 0})
                stop.wait(0.5)

        def writer(uname):
            ck, csrf = sessions[uname]
            ops = ["reserve", "cancel", "wait", "withdraw", "reschedule", "admin_lists", "mine"]
            while not stop.is_set():
                op = random.choice(ops)
                t0 = now()
                try:
                    if op == "reserve":
                        st, b, _ = req(args.port, "POST", "/api/reservations",
                                       {"slot_id": str(random.choice(slots)), "request_id": str(uuid.uuid4())},
                                       cookie=ck, csrf=csrf)
                        if st == 200 and b.get("data", {}).get("reservation_id"):
                            with lock: mine.setdefault(uname, []).append(b["data"]["reservation_id"])
                        if st == 200 and b.get("data", {}).get("status") == "PENDING":
                            req(args.port, "POST", f"/api/reservations/{b['data']['reservation_id']}/approve",
                                {"request_id": str(uuid.uuid4())}, cookie=adm_ck, csrf=adm_csrf)
                    elif op == "cancel":
                        with lock: rid = (mine.get(uname) or [None]).pop(0) if mine.get(uname) else None
                        if rid is None: continue
                        st, b, _ = req(args.port, "POST", f"/api/reservations/{rid}/cancel", {"request_id": str(uuid.uuid4())}, cookie=ck, csrf=csrf)
                    elif op == "wait":
                        st, b, _ = req(args.port, "POST", "/api/waitlist", {"slot_id": str(random.choice(slots)), "request_id": str(uuid.uuid4())}, cookie=ck, csrf=csrf)
                    elif op == "withdraw":
                        st, b, _ = req(args.port, "GET", "/api/me/records?page=1&page_size=20", cookie=ck, csrf=csrf)
                        wl = [w for w in (b.get("data", {}).get("waitlist") or []) if w.get("status") == "WAITING"]
                        if not wl: continue
                        st, b, _ = req(args.port, "POST", f"/api/waitlist/{wl[0]['id']}/withdraw", {"request_id": str(uuid.uuid4())}, cookie=ck, csrf=csrf)
                    elif op == "reschedule":
                        st, b, _ = req(args.port, "GET", "/api/me/records?page=1&page_size=20", cookie=ck, csrf=csrf)
                        rs = [x for x in (b.get("data", {}).get("reservations") or []) if x.get("status") == "CONFIRMED"]
                        if not rs: continue
                        st, b, _ = req(args.port, "POST", f"/api/reservations/{rs[0]['id']}/reschedule",
                                       {"slot_id": str(random.choice(slots)), "request_id": str(uuid.uuid4())}, cookie=ck, csrf=csrf)
                    elif op == "admin_lists":
                        st, b, _ = req(args.port, "GET", "/api/admin/records?status=PENDING&page=1&page_size=10", cookie=adm_ck, csrf=adm_csrf)
                        st, b, _ = req(args.port, "POST", "/api/admin/notifications", {"title": "负载", "body": "压测公告", "target": "ALL", "request_id": str(uuid.uuid4())}, cookie=adm_ck, csrf=adm_csrf)
                    else:
                        st, b, _ = req(args.port, "GET", "/api/me/records?page=1&page_size=20", cookie=ck, csrf=csrf)
                except Exception as e:
                    with lock: results["samples"].append({"op": op, "status": -1, "ms": round((now() - t0) * 1000, 2), "error": str(e)[:80]})
                    continue
                record(op, st, t0)

        probe = threading.Thread(target=wal_probe, daemon=True); probe.start()
        t_end = now() + args.duration
        threads = [threading.Thread(target=writer, args=(users[i % len(users)],), daemon=True) for i in range(args.concurrency)]
        for t in threads: t.start()
        while now() < t_end: time.sleep(0.5)
        stop.set()
        for t in threads: t.join(timeout=15)
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()

    samples = results["samples"]
    allms = [s["ms"] for s in samples]
    byop = {}
    for s in samples: byop.setdefault(s["op"], []).append(s)
    codes = {}
    for s in samples: codes[s["status"]] = codes.get(s["status"], 0) + 1
    wal = results["wal_bytes"]
    report = {
        "meta": {"date": results["started_at"], "duration_s": args.duration, "concurrency": args.concurrency,
                 "environment": "Windows x64 回环，本机实验证据口径"},
        "total_requests": len(samples),
        "status_codes": {str(k): v for k, v in sorted(codes.items())},
        "overall": {"p50": pct(allms, 50), "p95": pct(allms, 95), "p99": pct(allms, 99), "max": max(allms) if allms else None},
        "by_op": {op: {"n": len(v), "p50": pct([x["ms"] for x in v], 50), "p95": pct([x["ms"] for x in v], 95),
                       "p99": pct([x["ms"] for x in v], 99), "non_2xx": sum(1 for x in v if not 200 <= x["status"] < 300) if all(isinstance(x["status"], int) for x in v) else None}
                  for op, v in sorted(byop.items())},
        "wal_bytes": {"start": wal[0]["bytes"] if wal else 0, "end": wal[-1]["bytes"] if wal else 0,
                      "peak": max((x["bytes"] for x in wal), default=0)},
        "errors": sum(1 for s in samples if s["status"] == -1),
    }
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    dest = out / f"mixed-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入 {dest}")
    return 0 if report["errors"] == 0 and report["status_codes"].get("500", 0) == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
