"""基准实验：读性能与写争用（仅 Python 标准库，风格参照 integration.py）。"""
from __future__ import annotations
import argparse, concurrent.futures, contextlib, csv, datetime, http.client, json, math
import os, pathlib, shutil, socket, sqlite3, subprocess, tempfile, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASSWORD = os.environ.get("LAB_TEST_PASSWORD", "")  # 口令经环境变量传入，不落盘

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def uid():
    return str(uuid.uuid4())

def bj_today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

def pct(times, p):
    if not times: return 0.0
    s = sorted(times)
    idx = min(len(s) - 1, max(0, math.ceil(p / 100.0 * len(s)) - 1))
    return round(s[idx], 3)

class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None):
        h = {"Content-Type": "application/json"}
        if self.cookie: h["Cookie"] = self.cookie
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            t0 = time.perf_counter()
            conn.request(method, path, json.dumps(body).encode() if body is not None else None, h)
            resp = conn.getresponse(); ck = resp.getheader("Set-Cookie")
            if ck: self.cookie = ck.split(";", 1)[0]
            data = resp.read()
            return resp.status, json.loads(data), (time.perf_counter() - t0) * 1000
        finally:
            conn.close()

    def login(self, name):
        status, body, _ = self.request("POST", "/api/login", {"username": name, "password": PASSWORD})
        require(status == 200 and body["code"] == "OK", f"login {name}: {status}, {body}")
        self.csrf = body["data"]["csrf_token"]
        return self

class Server:
    def __init__(self, executable, directory, baseline):
        self.directory = pathlib.Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "test.db"; shutil.copy2(baseline, self.db)
        self.executable, self.process = executable, None

    def start(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); self.port = sock.getsockname()[1]
        self.log = open(self.directory / "server.log", "ab")
        self.process = subprocess.Popen([str(self.executable), "--db", str(self.db), "--web", str(ROOT / "web"), "--port", str(self.port)], stdout=self.log, stderr=subprocess.STDOUT)
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise AssertionError(f"server exited {self.process.returncode}")
            try:
                status, _, _ = Client(self.port).request("GET", "/api/health")
                if status == 200: return self
            except (OSError, http.client.HTTPException): pass
            time.sleep(.05)
        raise AssertionError("server health deadline")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
        if getattr(self, "log", None): self.log.close()

    def sql(self, query, params=()):
        with contextlib.closing(sqlite3.connect(self.db, timeout=5)) as db:
            with db:
                with contextlib.closing(db.execute(query, params)) as cur:
                    return cur.fetchall()

    def slots(self):
        return [str(r[0]) for r in self.sql("SELECT s.id FROM slots s WHERE s.start_at>? AND s.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at,id", (int(time.time()) + 3600,))]

def walled(fn, concurrency):
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        parts = list(ex.map(fn, range(concurrency)))
    return parts, (time.perf_counter() - start)

def aggregate(name, scenario, concurrency, rounds, samples, wall):
    times = [ms for _, ms in samples]
    total = len(samples)
    return {
        "experiment": name, "scenario": scenario, "concurrency": concurrency, "rounds": rounds, "requests": total,
        "throughput_rps": round(total / wall, 3) if wall > 0 else 0.0,
        "p50_ms": pct(times, 50), "p95_ms": pct(times, 95), "p99_ms": pct(times, 99),
        "count_200": sum(1 for s, _ in samples if 200 <= s < 300),
        "count_409": sum(1 for s, _ in samples if s == 409),
        "count_503": sum(1 for s, _ in samples if s == 503),
    }

def run_e1(server, concurrency, requests):
    clients = [Client(server.port).login(f"user{i:02d}") for i in range(1, concurrency + 1)]
    lab, today = "1", bj_today()
    def hit(i):
        c = clients[i]; row = []
        for j in range(requests):
            path = f"/api/slots?lab_id={lab}&date={today}" if j % 2 == 0 else "/api/me/records?page=1&page_size=20"
            status, _, ms = c.request("GET", path); row.append((status, ms))
        return row
    parts, wall = walled(hit, concurrency)
    return aggregate("E1", "read", concurrency, requests, [x for p in parts for x in p], wall)

def run_e2(server, concurrency, rounds):
    clients = [Client(server.port).login(f"user{i:02d}") for i in range(1, concurrency + 1)]
    slots = server.slots()
    need = rounds * (concurrency + 1)  # same 每轮 1 个 + diff 每轮 concurrency 个
    require(need <= len(slots), f"空闲场次不足：需要 {need}，实际 {len(slots)}（请减小 --requests）")
    same_slots = slots[:rounds]
    diff_slots = slots[rounds:rounds + rounds * concurrency]
    def same_hit(i):
        c = clients[i]; row = []
        for k in range(rounds):
            status, _, ms = c.request("POST", "/api/reservations", {"slot_id": same_slots[k], "request_id": uid()})
            row.append((status, ms))
        return row
    def diff_hit(i):
        c = clients[i]; row = []
        for k in range(rounds):
            status, _, ms = c.request("POST", "/api/reservations", {"slot_id": diff_slots[k * concurrency + i], "request_id": uid()})
            row.append((status, ms))
        return row
    same_parts, same_wall = walled(same_hit, concurrency)
    diff_parts, diff_wall = walled(diff_hit, concurrency)
    return (
        aggregate("E2", "same-slot", concurrency, rounds, [x for p in same_parts for x in p], same_wall),
        aggregate("E2", "diff-slot", concurrency, rounds, [x for p in diff_parts for x in p], diff_wall),
    )

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "build/lab-booking.exe")
    parser.add_argument("--clients", default="1,5,10,20", help="逗号分隔的并发数，默认 1,5,10,20")
    parser.add_argument("--requests", type=int, default=20, help="每个客户端的读请求数（E1）与并发轮数（E2），默认 20")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/benchmark")
    args = parser.parse_args()
    if len(PASSWORD) < 8: parser.error("请通过环境变量 LAB_TEST_PASSWORD 提供至少 8 位的测试专用口令")
    if not args.exe.is_file(): parser.error(f"Missing executable: {args.exe}")
    concurrency = [int(x) for x in args.clients.split(",") if x.strip()]
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    runs_dir = ROOT / "artifacts" / "benchmark-runs"; runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = pathlib.Path(tempfile.mkdtemp(prefix="bench-", dir=runs_dir))
    baseline = run_path / "baseline.db"
    env = os.environ.copy(); env["LAB_SEED_PASSWORD"] = PASSWORD
    seed = subprocess.run([str(args.exe), "--db", str(baseline), "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=30)
    require(seed.returncode == 0, f"seed failed: {seed.returncode}: {seed.stdout} {seed.stderr}")
    probe = Server(args.exe, run_path / "probe", baseline).start()
    try:
        available = len(probe.slots())
    finally:
        probe.stop()
    # E2 各档统一轮数，保证吞吐与分位数跨档可比（受最大并发档的空闲场次上限约束）
    shared_rounds = max(1, min(args.requests, available // (max(concurrency) + 1)))
    print(f"E2 统一轮数 = {shared_rounds}（可用空闲场次 {available}，最大并发 {max(concurrency)}）", flush=True)
    server = Server(args.exe, run_path / "srv", baseline).start()
    try:
        for c in concurrency:
            rows.append(run_e1(server, c, args.requests))
    finally:
        server.stop()
    for c in concurrency:  # E2 写争用每档使用独立数据库，避免前档占用污染后档
        s2 = Server(args.exe, run_path / f"e2-{c}", baseline).start()
        try:
            same, diff = run_e2(s2, c, shared_rounds)
            rows.extend([same, diff])
        finally:
            s2.stop()
    fields = ["experiment", "scenario", "concurrency", "rounds", "requests", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "count_200", "count_409", "count_503"]
    with (args.output / "benchmark.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    summary = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "executable": str(args.exe.resolve()), "clients": concurrency,
        "requests_per_client_e1": args.requests, "rounds_e2": shared_rounds, "available_slots": available,
        "raw_evidence_directory": str(run_path), "rows": rows,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Benchmark done: {len(rows)} 行 -> {args.output.resolve()}")
    for r in rows:
        print(f"  {r['experiment']}/{r['scenario']} c={r['concurrency']} 轮={r['rounds']} 请求={r['requests']} 吞吐={r['throughput_rps']} rps p50={r['p50_ms']} p99={r['p99_ms']} 200={r['count_200']} 409={r['count_409']} 503={r['count_503']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
