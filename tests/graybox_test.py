"""灰盒测试：经 HTTP 接口驱动业务操作，再直接读取 SQLite 校验落库正确性（仅 Python 标准库）。

介于黑盒（integration.py 只经 HTTP 观察）与白盒（unit.c 直调内部函数）之间：
本脚本掌握数据库结构，但所有状态变更都通过对外接口触发，再回到库里做交叉验证——
每个检查同时断言「HTTP 层面成功」与「库中确实这么写」，两者不一致即判失败。
结果写入 docs/evidence/graybox/graybox_results.json，退出码 0 表示全部通过。
"""
from __future__ import annotations
import argparse, contextlib, http.client, json, os, pathlib, shutil, socket, sqlite3, subprocess, tempfile, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASSWORD = os.environ.get("LAB_TEST_PASSWORD", "")
RESULTS = []

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def uid():
    return str(uuid.uuid4())

class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None):
        h = {"Content-Type": "application/json"}
        if self.cookie: h["Cookie"] = self.cookie
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, json.dumps(body).encode() if body is not None else None, h)
            resp = conn.getresponse(); ck = resp.getheader("Set-Cookie")
            if ck: self.cookie = ck.split(";", 1)[0]
            data = resp.read()
            try: parsed = json.loads(data)
            except ValueError: raise AssertionError(f"非 JSON 响应 {resp.status}: {data[:160]!r}")
            return resp.status, parsed
        finally:
            conn.close()

    def login(self, name):
        st, body = self.request("POST", "/api/login", {"username": name, "password": PASSWORD})
        require(st == 200 and body["code"] == "OK", f"login {name}: {st}, {body}")
        self.csrf = body["data"]["csrf_token"]
        return self

    def post(self, path, body, expect=200):
        st, data = self.request("POST", path, body)
        require(st == expect, f"POST {path}: 期望 {expect}，实际 {st} {data}")
        return data

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
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise AssertionError(f"server exited {self.process.returncode}")
            try:
                if Client(self.port).request("GET", "/api/health")[0] == 200: return self
            except (OSError, http.client.HTTPException): pass
            time.sleep(.05)
        raise AssertionError("server health deadline")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
        if getattr(self, "log", None): self.log.close()

    def one(self, query, params=()):
        rows = self.sql(query, params)
        require(len(rows) == 1, f"期望恰好 1 行，实际 {len(rows)}：{query}")
        return rows[0]

    def sql(self, query, params=()):
        with contextlib.closing(sqlite3.connect(self.db, timeout=5)) as db:
            with db:
                with contextlib.closing(db.execute(query, params)) as cur:
                    return cur.fetchall()

    def free_slots(self, limit=8):
        return [str(r[0]) for r in self.sql("SELECT s.id FROM slots s WHERE s.start_at>? AND s.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at,id LIMIT ?", (int(time.time()) + 3600, limit))]

def record(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "passed": True, "detail": detail})
        print(f"  PASS  {name}" + (f"  ->  {detail}" if detail else ""), flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

def run(server):
    admin = Client(server.port).login("admin")
    a = Client(server.port).login("user01")
    b = Client(server.port).login("user02")
    state = {}

    def c1_register():
        name = f"gb{uid()[:8]}"
        c = Client(server.port)
        st, body = c.request("POST", "/api/register", {"username": name, "password": PASSWORD})
        require(st == 200 and body["code"] == "OK", f"注册失败：{st} {body}")
        state["new_user"] = body["data"]["user"]["id"]
        state["new_name"] = name
        row = server.one("SELECT username,password_hash,role,enabled FROM users WHERE id=?", (state["new_user"],))
        require(row[0] == name, f"users.username 应为 {name}，实际 {row[0]}")
        require(row[1].startswith("$argon2id$"), f"password_hash 应为 argon2id 前缀，实际前 20 字符 {row[1][:20]!r}")
        require(row[2] == "USER", f"role 应为 USER，实际 {row[2]}")
        require(row[3] == 1, f"新注册用户 enabled 应为 1，实际 {row[3]}")
        ev = server.one("SELECT action,actor_id FROM operation_events WHERE action='REGISTER' AND actor_id=?", (state["new_user"],))
        require(ev[0] == "REGISTER", "operation_events 缺少 REGISTER 记录")
        return f"user={state['new_user']} role=USER hash={row[1][:18]}..."

    def c2_reserve():
        state["slot"] = server.free_slots(1)[0]
        st, body = a.request("POST", "/api/reservations", {"slot_id": state["slot"], "request_id": uid()})
        require(st == 200 and body["code"] == "OK", f"预约失败：{st} {body}")
        state["rid"] = body["data"]["reservation_id"]
        row = server.one("SELECT user_id,slot_id,status,source,cancelled_at,checked_in_at FROM reservations WHERE id=?", (state["rid"],))
        require(str(row[1]) == str(state["slot"]), f"slot_id 应为 {state['slot']}，实际 {row[1]}")
        require(row[2] == "CONFIRMED", f"status 应为 CONFIRMED，实际 {row[2]}")
        require(row[3] == "DIRECT", f"source 应为 DIRECT，实际 {row[3]}")
        require(row[4] is None and row[5] is None, "新建预约不应有 cancelled_at/checked_in_at")
        owner = server.one("SELECT id FROM users WHERE username='user01'")[0]
        require(row[0] == owner, f"user_id 应为 user01({owner})，实际 {row[0]}")
        server.one("SELECT id FROM operation_events WHERE action='RESERVE' AND entity_id=?", (state["rid"],))
        return f"reservation={state['rid']} CONFIRMED/DIRECT"

    def c3_waitlist():
        st, body = b.request("POST", "/api/waitlist", {"slot_id": state["slot"], "request_id": uid()})
        require(st == 200 and body["code"] == "OK", f"候补失败：{st} {body}")
        state["wid"] = body["data"]["waitlist_id"]
        row = server.one("SELECT status,promoted_reservation_id,slot_id FROM waitlist WHERE id=?", (state["wid"],))
        require(row[0] == "WAITING", f"waitlist.status 应为 WAITING，实际 {row[0]}")
        require(row[1] is None, "尚未补位时 promoted_reservation_id 应为 NULL")
        require(str(row[2]) == str(state["slot"]), "waitlist.slot_id 应与预约同一场次")
        server.one("SELECT id FROM operation_events WHERE action='WAIT' AND entity_id=?", (state["wid"],))
        return f"waitlist={state['wid']} WAITING"

    def c4_cancel():
        st, body = a.request("POST", f"/api/reservations/{state['rid']}/cancel", {"request_id": uid()})
        require(st == 200 and body["code"] == "OK", f"取消失败：{st} {body}")
        state["promoted"] = body["data"]["promoted_reservation_id"]
        row = server.one("SELECT status,cancelled_at,cancel_reason FROM reservations WHERE id=?", (state["rid"],))
        require(row[0] == "CANCELLED", f"status 应为 CANCELLED，实际 {row[0]}")
        require(row[1] is not None, "cancelled_at 不应为空")
        require(row[2] == "USER", f"cancel_reason 应为 USER，实际 {row[2]}")
        server.one("SELECT id FROM operation_events WHERE action='CANCEL' AND entity_id=?", (state["rid"],))
        return f"reservation={state['rid']} CANCELLED/USER cancelled_at={row[1]}"

    def c5_promote():
        require(state.get("promoted"), f"取消失败时未返回补位预约 id：{state.get('promoted')!r}")
        w = server.one("SELECT status,promoted_reservation_id FROM waitlist WHERE id=?", (state["wid"],))
        require(w[0] == "PROMOTED", f"waitlist.status 应为 PROMOTED，实际 {w[0]}")
        require(str(w[1]) == str(state["promoted"]), f"waitlist.promoted_reservation_id 应为 {state['promoted']}，实际 {w[1]}")
        r = server.one("SELECT status,source,user_id,slot_id FROM reservations WHERE id=?", (state["promoted"],))
        require(r[0] == "CONFIRMED", f"补位预约状态应为 CONFIRMED，实际 {r[0]}")
        require(r[1] == "WAITLIST", f"补位预约 source 应为 WAITLIST，实际 {r[1]}")
        require(str(r[3]) == str(state["slot"]), "补位预约应占用同一场次")
        waiter = server.one("SELECT id FROM users WHERE username='user02'")[0]
        require(r[2] == waiter, f"补位预约应属于候补者 user02({waiter})，实际 {r[2]}")
        server.one("SELECT id FROM operation_events WHERE action='PROMOTE' AND entity_id=?", (state["promoted"],))
        occupied = server.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'", (state["slot"],))[0][0]
        require(occupied == 1, f"该场次有效占用应恰为 1，实际 {occupied}")
        return f"waitlist={state['wid']} PROMOTED -> reservation={state['promoted']} WAITLIST"

    def c6_notification():
        waiter = server.one("SELECT id FROM users WHERE username='user02'")[0]
        row = server.one("SELECT kind,slot_id,reservation_id FROM notifications WHERE user_id=? AND kind='PROMOTED' ORDER BY id DESC LIMIT 1", (waiter,))
        require(row[0] == "PROMOTED", f"通知 kind 应为 PROMOTED，实际 {row[0]}")
        require(str(row[2]) == str(state["promoted"]), "通知应指向补位产生的预约")
        apis = b.request("GET", "/api/me/notifications?page=1&page_size=5")[1]["data"]["notifications"]
        require(any(n["kind"] == "PROMOTED" for n in apis), "通知应能经接口读出")
        return f"notification kind=PROMOTED reservation={row[2]}"

    def c7_checkin():
        slot = server.free_slots(1)[0]
        st, body = a.request("POST", "/api/reservations", {"slot_id": slot, "request_id": uid()})
        require(st == 200, f"准备签到的预约失败：{st} {body}")
        rid = body["data"]["reservation_id"]
        now = int(time.time())
        server.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?", (now - 1, now + 3599, slot))
        st, body = a.request("POST", f"/api/reservations/{rid}/checkin", {"request_id": uid()})
        require(st == 200 and body["code"] == "OK", f"签到失败：{st} {body}")
        row = server.one("SELECT status,checked_in_at FROM reservations WHERE id=?", (rid,))
        require(row[0] == "CONFIRMED", f"签到后状态仍应为 CONFIRMED，实际 {row[0]}")
        require(row[1] is not None, "checked_in_at 应被写入")
        require(row[1] == int(body["data"]["checked_in_at"]), "接口返回的签到时间应与库中一致")
        server.one("SELECT id FROM operation_events WHERE action='CHECKIN' AND entity_id=?", (rid,))
        return f"reservation={rid} checked_in_at={row[1]}"

    def c8_password():
        before = server.one("SELECT password_hash FROM users WHERE username='user05'")
        c = Client(server.port).login("user05")
        other = Client(server.port).login("user05")
        old_hash = server.one("SELECT password_hash FROM users WHERE username='user05'")[0]
        st, body = c.request("POST", "/api/me/password", {"old_password": PASSWORD, "new_password": PASSWORD + "-new", "request_id": uid()})
        require(st == 200 and body["code"] == "OK", f"改密失败：{st} {body}")
        new_hash = server.one("SELECT password_hash FROM users WHERE username='user05'")[0]
        require(new_hash != old_hash, "users.password_hash 应被更新")
        require(new_hash.startswith("$argon2id$"), "新哈希应仍为 argon2id")
        require(other.request("GET", "/api/me")[0] == 401, "改密后其他会话应失效")
        uid5 = server.one("SELECT id FROM users WHERE username='user05'")[0]
        server.one("SELECT id FROM operation_events WHERE action='PASSWORD_CHANGE' AND actor_id=?", (uid5,))
        c.post("/api/me/password", {"old_password": PASSWORD + "-new", "new_password": PASSWORD, "request_id": uid()})
        return "password_hash 已轮换且旧会话失效"

    for name, fn in [("注册落库（argon2id/role/enabled + REGISTER 事件）", c1_register),
                     ("预约落库（CONFIRMED/DIRECT + RESERVE 事件）", c2_reserve),
                     ("候补落库（WAITING + WAIT 事件）", c3_waitlist),
                     ("取消落库（cancelled_at/cancel_reason=USER + CANCEL 事件）", c4_cancel),
                     ("补位落库（PROMOTED + 新行 WAITLIST + PROMOTE 事件）", c5_promote),
                     ("通知落库（kind=PROMOTED 且可经接口读出）", c6_notification),
                     ("签到落库（checked_in_at 与接口一致 + CHECKIN 事件）", c7_checkin),
                     ("改密落库（哈希轮换 + 旧会话失效 + PASSWORD_CHANGE 事件）", c8_password)]:
        record(name, fn)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "build/lab-booking.exe")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/graybox")
    args = parser.parse_args()
    if len(PASSWORD) < 8: parser.error("请通过环境变量 LAB_TEST_PASSWORD 提供至少 8 位的测试专用口令")
    if not args.exe.is_file(): parser.error(f"Missing executable: {args.exe}")
    args.output.mkdir(parents=True, exist_ok=True)
    runs = ROOT / "artifacts" / "graybox-runs"; runs.mkdir(parents=True, exist_ok=True)
    run_path = pathlib.Path(tempfile.mkdtemp(prefix="graybox-", dir=runs))
    baseline = run_path / "baseline.db"
    env = os.environ.copy(); env["LAB_SEED_PASSWORD"] = PASSWORD
    seed = subprocess.run([str(args.exe), "--db", str(baseline), "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=60)
    require(seed.returncode == 0, f"seed failed: {seed.returncode}: {seed.stdout} {seed.stderr}")
    server = Server(args.exe, run_path / "srv", baseline).start()
    try:
        run(server)
    finally:
        server.stop()
    passed = sum(1 for r in RESULTS if r["passed"])
    report = {
        "timestamp_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "executable": str(args.exe.resolve()), "checks_total": len(RESULTS), "checks_passed": passed,
        "all_passed": passed == len(RESULTS), "results": RESULTS,
    }
    (args.output / "graybox_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n灰盒检查 {passed}/{len(RESULTS)} 通过")
    print(f"证据：{(args.output / 'graybox_results.json').resolve()}")
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
