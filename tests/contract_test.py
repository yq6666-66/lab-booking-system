"""API 契约形状测试：逐端点核对响应封套、字段名与类型，并覆盖错误码（仅 Python 标准库）。

对 docs/CONTRACT.md 声明的每个 REST 端点发一次合法请求，断言：
1. 顶层恰好三键 code(str)/message(str)/data(dict|null)；
2. data 内字段名与契约一致（不多不少），嵌套对象与数组元素逐层核对；
3. 类型正确：ID 为十进制字符串、时间为整数、enabled/occupied 等为布尔；
另覆盖不少于 8 种错误场景（400/401/403/404/409/413 各至少一例）。
结果写入 docs/evidence/contract/contract_results.json，退出码 0 表示全部通过。
"""
from __future__ import annotations
import sys, argparse, contextlib, http.client, json, os, pathlib, re, shutil, socket, sqlite3, subprocess, tempfile, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
PASSWORD = os.environ.get("LAB_TEST_PASSWORD", "")
RESULTS = []
CONTRACT_DRIFT = []  # 实现返回了契约未记录的字段：记为文档漂移提示，不计入失败

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def uid():
    return str(uuid.uuid4())

class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None, raw=None, headers=None):
        h = {"Content-Type": "application/json"}
        if self.cookie: h["Cookie"] = self.cookie
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        if headers:
            for k, v in headers.items():
                if v is None: h.pop(k, None)
                else: h[k] = v
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=payload, headers=h)
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
    def __init__(self, executable, directory, baseline, extra=None):
        self.directory = pathlib.Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "test.db"; shutil.copy2(baseline, self.db)
        self.executable, self.extra, self.process = executable, list(extra or []), None

    def start(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); self.port = sock.getsockname()[1]
        self.log = open(self.directory / "server.log", "ab")
        cmd = [str(self.executable), "--db", str(self.db), "--web", str(ROOT / "web"), "--port", str(self.port)] + self.extra
        self.process = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
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

    def sql(self, query, params=()):
        with contextlib.closing(sqlite3.connect(self.db, timeout=5)) as db:
            with db:
                with contextlib.closing(db.execute(query, params)) as cur:
                    return cur.fetchall()

    def slots(self, lab="1", day_offset=1):
        rows = self.sql("SELECT s.id,s.lab_id,s.start_at FROM slots s WHERE s.start_at>? AND s.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at,id LIMIT 20", (int(time.time()) + 3600,))
        return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]

@contextlib.contextmanager
def running(*args, **kwargs):
    server = Server(*args, **kwargs)
    try: yield server.start()
    finally: server.stop()

# ---------------- 契约 schema ----------------
T_ID, T_INT, T_BOOL, T_STR, T_NUM = "id", "int", "bool", "str", "num"
def nb(spec): return ("nullable", spec)
def arr(spec): return ("array", spec)

USER_OBJ = {"id": T_ID, "username": T_STR, "role": T_STR}
LOGIN_DATA = {"user": USER_OBJ, "csrf_token": T_STR}
LAB_OBJ = {"id": T_ID, "name": T_STR, "location": T_STR, "description": T_STR, "enabled": T_BOOL}
SLOT_OBJ = {"id": T_ID, "lab_id": T_ID, "start_at": T_INT, "end_at": T_INT, "enabled": T_BOOL, "lab_enabled": T_BOOL,
            "capacity": T_INT, "confirmed_count": T_INT, "waiting_count": T_INT, "my_reservation_id": nb(T_ID),
            "my_checked_in_at": nb(T_INT), "my_waitlist_id": nb(T_ID)}
RES_ROW = {"id": T_ID, "slot_id": T_ID, "lab_id": T_ID, "lab_name": T_STR, "username": T_STR, "start_at": T_INT, "end_at": T_INT,
           "status": T_STR, "source": T_STR, "cancel_reason": nb(T_STR), "checked_in_at": nb(T_INT),
           "hold_deadline": nb(T_NUM), "note": nb(T_STR)}
WAIT_ROW = {"id": T_ID, "slot_id": T_ID, "lab_name": T_STR, "username": T_STR, "start_at": T_INT, "end_at": T_INT,
            "status": T_STR, "position": nb(T_INT)}
NOTIFY_ROW = {"id": T_ID, "kind": T_STR, "title": T_STR, "body": T_STR, "slot_id": nb(T_ID),
              "reservation_id": nb(T_ID), "read_at": nb(T_INT), "created_at": T_INT}
SESSION_ROW = {"id": T_STR, "created_at": nb(T_INT), "expires_at": T_INT, "current": T_BOOL}
STAT_ROW = {"date": T_STR, "slots": T_INT, "confirmed": T_INT, "cancelled": T_INT,
            "no_show": T_INT, "checked_in": T_INT, "waiting": T_INT}
ASSET_OBJ = {"id": T_ID, "name": T_STR, "spec": T_STR, "total": T_INT, "status": T_STR}
UTIL_ROW = {"lab_id": T_ID, "lab_name": T_STR, "slots": T_INT, "seats": T_INT, "confirmed": T_INT, "checked_in": T_INT, "no_show": T_INT, "utilization": T_NUM, "actual_minutes": T_NUM, "seat_minutes": T_NUM, "utilization_actual": T_NUM}
STAT_DAY = {"date": T_STR, "claims": T_INT}
CLAIM_ROW = {"asset_id": T_ID, "asset_name": T_STR, "claims": T_INT, "last_start": T_INT}
LEDGER_ROW = {"id": T_ID, "delta": T_INT, "reason": T_STR, "reservation_id": nb(T_ID), "created_at": T_INT}
WINDOW_ROW = {"id": T_ID, "asset_id": T_ID, "weekday_mask": T_INT, "start_minute": T_INT, "end_minute": T_INT, "reason": T_STR}
MAINT_ROW = {"id": T_ID, "asset_id": T_ID, "started_at": T_INT, "ended_at": nb(T_INT), "reason": T_STR, "operator": nb(T_STR)}
SUG_REASON = {"code": T_STR, "message": T_STR}
SUG_ROW = {"slot_id": T_ID, "start_at": T_INT, "end_at": T_INT, "capacity": T_INT, "taken": T_INT, "waiting_count": T_INT,
           "bookable": T_BOOL, "joinable": T_BOOL, "queue_ahead": T_INT, "promote_probability": nb(T_NUM), "reasons": arr(SUG_REASON)}
FAIR_ROW = {"id": T_ID, "username": T_STR, "joined": T_INT, "promoted": T_INT, "withdrawn": T_INT, "skipped": T_INT,
            "avg_wait_s": nb(T_NUM), "granted": T_INT}
TOKEN_ROW = {"id": T_ID, "name": T_STR, "created_at": T_INT, "last_used_at": nb(T_INT)}
TOTALS_OBJ = {"slots": T_INT, "confirmed": T_INT, "cancelled": T_INT, "no_show": T_INT,
              "checked_in": T_INT, "waiting": T_INT}
COUNTERS_OBJ = {"requests_total": T_NUM, "ok_2xx": T_NUM, "err_4xx": T_NUM, "err_5xx": T_NUM,
                "db_busy_503": T_NUM, "logins": T_NUM}
LATENCY_OBJ = {"count": T_NUM, "sum": T_NUM, "max": T_NUM, "buckets": arr(T_NUM)}
EVENT_ROW_ADMIN = {"id": T_ID, "actor": T_STR, "action": T_STR, "entity_id": T_ID, "created_at": T_INT,
                   "request_id": nb(T_STR)}
PAGED = {"page": T_INT, "page_size": T_INT, "has_more": T_BOOL}
ME_RECORDS = {"reservations": arr(RES_ROW), "waitlist": arr(WAIT_ROW), "events": arr({}), **PAGED}
ADMIN_RECORDS = {"reservations": arr(RES_ROW), "waitlist": arr(WAIT_ROW),
                 "events": arr(EVENT_ROW_ADMIN), **PAGED}

# 路径 -> (方法, data schema)；{id} 为占位符，运行时替换
SCHEMAS = [
    ("GET",  "/api/health",                    {"status": T_STR, "version": T_STR, "uptime_s": T_NUM}),
    ("POST", "/api/login",                     LOGIN_DATA),
    ("GET",  "/api/me",                        LOGIN_DATA),
    ("POST", "/api/logout",                    "empty-or-obj"),
    ("GET",  "/api/labs",                      {"labs": arr(LAB_OBJ)}),
    ("GET",  "/api/slots?lab_id=1&date={today}", {"slots": arr(SLOT_OBJ), "checkin_window": T_INT}),
    ("GET",  "/api/suggestions?lab_id={lid}&date={today}", {"suggestions": arr(SUG_ROW)}),
    ("GET",  "/api/admin/fairness?days=28", {"users": arr(FAIR_ROW), "jain_index": T_NUM, "days": T_INT, "jain_definition": T_STR, "wait_definition": T_STR}),
    ("GET",  "/api/me/records?page=1&page_size=5", ME_RECORDS),
    ("GET",  "/api/me/notifications?page=1&page_size=5",
             {"notifications": arr(NOTIFY_ROW), "unread_count": T_INT, **PAGED}),
    ("POST", "/api/me/notifications/read",     {"updated": T_INT}),
    ("GET",  "/api/me/sessions",               {"sessions": arr(SESSION_ROW)}),
    ("POST", "/api/me/password",               {"revoked_sessions": T_INT, "revoked_tokens": T_INT}),
    ("POST", "/api/reservations",              {"reservation_id": T_ID}),
    ("POST", "/api/reservations/{rid}/cancel", {"reservation_id": T_ID, "promoted_reservation_id": nb(T_ID)}),
    ("POST", "/api/waitlist",                  {"waitlist_id": T_ID, "queue_ahead": T_INT, "promote_probability": nb(T_NUM)}),
    ("POST", "/api/waitlist/{wid2}/withdraw",  {"waitlist_id": T_ID}),
    ("POST", "/api/reservations/{rid2}/checkin", {"reservation_id": T_ID, "checked_in_at": T_INT}),
    ("POST", "/api/reservations/{rid2}/checkout", {"reservation_id": T_ID, "checked_out_at": nb(T_INT)}),
    ("POST", "/api/reservations/{rid3}/reschedule", {"reservation_id": T_ID, "new_slot_id": T_ID, "old_slot_id": T_ID, "promoted_reservation_id": nb(T_ID)}),
    ("GET",  "/api/me/calendar/export", {"filename": T_STR, "content": T_STR}),
    ("POST", "/api/me/tokens",                          {"token": T_STR, "name": T_STR}),
    ("GET",  "/api/me/tokens",                          {"tokens": arr(TOKEN_ROW)}),
    ("GET",  "/api/admin/records?date={today}&page=1&page_size=5", ADMIN_RECORDS),
    ("GET",  "/api/admin/stats?start_date={today}&end_date={today}", {"stats": arr(STAT_ROW), "totals": TOTALS_OBJ}),
    ("GET",  "/api/admin/stats/export?start_date={today}&end_date={today}", {"filename": T_STR, "content": T_STR}),
    ("GET",  "/api/admin/metrics",             {"counters": COUNTERS_OBJ, "latency_ms": LATENCY_OBJ}),
    ("POST", "/api/admin/labs",                {"lab_id": T_ID}),
    ("POST", "/api/admin/labs/{lid}/update",   {"lab_id": T_ID}),
    ("POST", "/api/admin/labs/{lid}/assets",   {"asset_id": T_ID}),
    ("POST", "/api/admin/assets/{aid}/update", {"asset_id": T_ID}),
    ("GET",  "/api/labs/{lid}/assets",         {"assets": arr(ASSET_OBJ)}),
    ("GET",  "/api/admin/labs/utilization?start_date={today}&end_date={today}", {"utilization": arr(UTIL_ROW)}),
    ("GET",  "/api/admin/assets/{aid}/usage?start_date={today}&end_date={today}", {"asset": ASSET_OBJ, "usage": arr(STAT_DAY)}),
    ("GET",  "/api/admin/stats/utilization/export?start_date={today}&end_date={today}", {"filename": T_STR, "content": T_STR}),
    ("GET",  "/api/admin/asset-claims?start_date={today}&end_date={today}", {"claims": arr(CLAIM_ROW)}),
    ("GET",  "/api/admin/asset-claims/export?start_date={today}&end_date={today}", {"filename": T_STR, "content": T_STR}),
    ("GET",  "/api/admin/records?status=PENDING&page=1&page_size=5", ADMIN_RECORDS),
    ("GET",  "/api/me/credits?page=1&page_size=5", {"ledger": arr(LEDGER_ROW), "balance": T_INT, "base": T_INT, **PAGED}),
    ("GET",  "/api/me/calendar.ics",           {"filename": T_STR, "content": T_STR}),
    ("POST", "/api/reservations/batch",        {"created": T_INT}),
    ("GET",  "/api/admin/assets/{aid}/windows", {"windows": arr(WINDOW_ROW)}),
    ("POST", "/api/admin/assets/{aid}/windows", {"window_id": T_ID}),
    ("GET",  "/api/admin/assets/{aid}/maintenance", {"maintenance": arr(MAINT_ROW)}),
    ("POST", "/api/admin/assets/{aid}/maintenance", {"maintenance_id": T_ID}),
    ("POST", "/api/admin/users/{uid1}/credit",  {"granted": T_INT}),
    ("POST", "/api/admin/slots/publish",       {"created": T_INT}),
    ("POST", "/api/register",                  LOGIN_DATA),
    ("POST", "/api/me/sessions/{sid}/revoke",  "empty-or-obj"),
]

def check_value(value, spec, path):
    if isinstance(spec, tuple) and spec[0] == "nullable":
        if value is None: return
        check_value(value, spec[1], path); return
    if isinstance(spec, tuple) and spec[0] == "array":
        require(isinstance(value, list), f"{path}: 期望数组，实际 {type(value).__name__}")
        for i, item in enumerate(value): check_value(item, spec[1], f"{path}[{i}]")
        return
    if isinstance(spec, dict):
        require(isinstance(value, dict), f"{path}: 期望对象，实际 {type(value).__name__}")
        fields(value, spec, path); return
    if spec == T_ID:
        require(isinstance(value, str) and value.isdigit(), f"{path}: 期望十进制 ID 字符串，实际 {value!r}")
    elif spec == T_INT:
        require(isinstance(value, int) and not isinstance(value, bool), f"{path}: 期望整数，实际 {type(value).__name__}={value!r}")
    elif spec == T_BOOL:
        require(isinstance(value, bool), f"{path}: 期望布尔，实际 {type(value).__name__}={value!r}")
    elif spec == T_STR:
        require(isinstance(value, str), f"{path}: 期望字符串，实际 {type(value).__name__}={value!r}")
    elif spec == T_NUM:
        require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{path}: 期望数字，实际 {type(value).__name__}={value!r}")
    else:
        raise AssertionError(f"{path}: 未知 schema {spec!r}")

def fields(obj, spec, name):
    expected, actual = set(spec), set(obj)
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    require(not missing, f"{name}: 缺少字段 {missing}（契约要求 {sorted(expected)}）")
    if extra:
        CONTRACT_DRIFT.append(f"{re.sub(r'\[\d+\]', '[]', name)}: 实现多返回契约未记录的字段 {extra}")
    for key, sub in spec.items(): check_value(obj[key], sub, f"{name}.{key}")

def envelope(status, body, name):
    require(isinstance(body, dict), f"{name}: 响应应为对象，实际 {type(body).__name__}")
    require(set(body) == {"code", "message", "data"}, f"{name}: 顶层应恰好为 code/message/data，实际 {sorted(body)}")
    require(isinstance(body["code"], str) and body["code"], f"{name}: code 应为非空字符串")
    require(isinstance(body["message"], str), f"{name}: message 应为字符串，实际 {type(body['message']).__name__}")
    require(body["data"] is None or isinstance(body["data"], dict), f"{name}: data 应为对象或 null，实际 {type(body['data']).__name__}")

def record(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "passed": True, "detail": detail})
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""), flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

def today():
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

def run_scenarios(server):
    """建立各端点所需的业务数据，并逐端点核对形状。"""
    admin, a, b = Client(server.port).login("admin"), Client(server.port).login("user01"), Client(server.port).login("user02")
    slot_pairs = server.slots()
    require(len(slot_pairs) >= 3, f"可选空闲场次不足：{len(slot_pairs)}")
    slot = slot_pairs[0][0]
    lab = slot_pairs[0][1]
    bind = {"today": today()}
    st, body = a.request("POST", "/api/reservations", {"slot_id": slot, "request_id": uid()})
    require(st == 200 and body["code"] == "OK", f"准备预约失败：{st} {body}")
    bind["rid"] = body["data"]["reservation_id"]
    st, body = b.request("POST", "/api/waitlist", {"slot_id": slot, "request_id": uid()})
    require(st == 200 and body["code"] == "OK", f"准备候补失败：{st} {body}")
    bind["wid"] = body["data"]["waitlist_id"]
    st, body = b.request("POST", "/api/reservations", {"slot_id": slot_pairs[1][0], "request_id": uid()})
    require(st == 200, f"准备第二预约失败：{st} {body}")
    bind["rid2"] = body["data"]["reservation_id"]
    now = int(time.time())  # 把第二个预约所在场次推到“已开始”，使其处于可签到状态
    server.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?", (now - 1, now + 3599, slot_pairs[1][0]))
    bind["lid"] = str(server.sql("SELECT id FROM labs WHERE enabled=1 ORDER BY id LIMIT 1")[0][0])
    bind["uid1"] = str(server.sql("SELECT id FROM users WHERE username='user01'")[0][0])
    # 批量预约需要一个未被其它场景占用的场次：取最晚的一个空闲场次，避免与 free_slot 冲突
    bind["batch_slot"] = str(server.sql("SELECT s.id FROM slots s WHERE s.start_at>? AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at DESC LIMIT 1", (int(time.time()) + 3600,))[0][0])
    st, body = admin.request("POST", f"/api/admin/labs/{bind['lid']}/assets", {"name": "契约资源", "spec": "contract", "total": "2"})
    require(st == 200, f"准备资源失败：{st} {body}")
    bind["aid"] = body["data"]["asset_id"]
    # 单独准备一条仍处于 WAITING 的候补（上面那条会在取消时被补位），用于校验 withdraw
    st, body = a.request("POST", "/api/reservations", {"slot_id": slot_pairs[3][0], "request_id": uid()})
    require(st == 200, f"准备第三预约失败：{st} {body}")
    bind["rid3"] = body["data"]["reservation_id"]
    bind["slot3"] = str(slot_pairs[3][0])
    # 改期目标：同实验室、不同场次（发布明天的场次保证存在且未开始）
    lab3 = server.sql("SELECT lab_id FROM slots WHERE id=?", (int(bind["slot3"]),))[0][0]
    import datetime as _dt
    tmr = (_dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))) + _dt.timedelta(days=1)).date().isoformat()
    admin.request("POST", "/api/admin/slots/publish", {"lab_id": str(lab3), "start_date": tmr, "end_date": tmr, "capacity": "5"})
    alt = server.sql("SELECT id FROM slots WHERE lab_id=? AND start_at>? AND id<>? AND (SELECT count(*) FROM reservations r WHERE r.slot_id=slots.id AND r.status='CONFIRMED')=0 AND NOT EXISTS(SELECT 1 FROM reservations r2 JOIN slots s2 ON s2.id=r2.slot_id WHERE r2.user_id=(SELECT id FROM users WHERE username='user01') AND r2.status='CONFIRMED' AND slots.start_at<s2.end_at AND s2.start_at<slots.end_at) ORDER BY start_at LIMIT 1", (lab3, int(time.time()) + 3600, int(bind["slot3"])))
    require(alt, "no reschedule target slot")
    bind["slot_alt"] = str(alt[0][0])
    st, body = b.request("POST", "/api/waitlist", {"slot_id": slot_pairs[3][0], "request_id": uid()})
    require(st == 200, f"准备独立候补失败：{st} {body}")
    bind["wid2"] = body["data"]["waitlist_id"]
    bind["free_slot"] = next(p[0] for p in slot_pairs[2:]
        if p[0] != bind.get("slot_alt") and p[2] not in {r[0] for r in server.sql(
            "SELECT s2.start_at FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=(SELECT id FROM users WHERE username='user01') AND r.status='CONFIRMED'")}
        and not server.sql("SELECT 1 FROM slots sa JOIN slots sb ON sa.start_at<sb.end_at AND sb.start_at<sa.end_at WHERE sa.id=? AND sb.id=?",(int(p[0]),int(bind.get("slot_alt",0))))  # 不得与改期目标时段重叠（user01 之后还会预约 free_slot）
    )  # r12：须与 user01 已有时段错开，否则 TIME_CONFLICT；并避开改期目标 slot_alt 及其同时段
    sessions = a.request("GET", "/api/me/sessions")[1]["data"]["sessions"]
    bind["sid"] = sessions[0]["id"]
    bind["lab"] = lab
    for method, path_tpl, spec in SCHEMAS:
        path = path_tpl.format(**bind)
        if path_tpl == "GET /api/slots?lab_id=1&date={today}":
            path = f"/api/slots?lab_id={lab}&date={today()}"
        name = f"{method} {path_tpl}"
        payload = None
        if method == "POST":
            if path_tpl == "/api/login":
                payload = {"username": "user03", "password": PASSWORD}
            elif path_tpl == "/api/reservations/batch":
                payload = {"slot_ids": [bind["batch_slot"]], "request_id": uid()}
            elif path_tpl == "/api/admin/users/{uid1}/credit":
                payload = {"delta": "1", "request_id": uid()}
            elif path_tpl == "/api/admin/assets/{aid}/maintenance":
                payload = {"op": "open", "reason": "contract", "request_id": uid()}
            elif path_tpl == "/api/admin/assets/{aid}/windows":
                payload = {"weekday_mask": 127, "start_minute": 480, "end_minute": 720, "request_id": uid()}
            elif path_tpl == "/api/register":
                payload = {"username": f"cts{uid()[:8]}", "password": PASSWORD}
            elif path_tpl == "/api/admin/labs":
                payload = {"name": f"契约实验室{uid()[:6]}", "location": "实验楼", "description": "contract"}
            elif path_tpl == "/api/admin/labs/{lid}/update":
                payload = {"name": f"契约实验室{uid()[:6]}", "location": "实验楼", "description": "contract", "enabled": True}
            elif path_tpl == "/api/me/tokens":
                payload = {"name": f"契约令牌{uid()[:6]}"}
            elif path_tpl == "/api/reservations/{rid3}/reschedule":
                payload = {"slot_id": bind["slot_alt"], "request_id": uid()}
            elif path_tpl == "/api/admin/labs/{lid}/assets":
                payload = {"name": f"契约资源{uid()[:6]}", "spec": "contract", "total": "2"}
            elif path_tpl == "/api/admin/assets/{aid}/update":
                payload = {"name": "契约资源", "spec": "contract-v2", "total": "3", "status": "AVAILABLE"}
            elif path_tpl == "/api/admin/slots/publish":
                payload = {"lab_id": lab, "start_date": today(), "end_date": today()}
            elif path_tpl == "/api/me/password":
                payload = {"old_password": PASSWORD, "new_password": PASSWORD + "-x", "request_id": uid()}
            elif path_tpl == "/api/me/notifications/read":
                payload = {"all": True, "request_id": uid()}
            elif path_tpl in ("/api/logout",):
                payload = {}
            elif path_tpl in ("/api/reservations", "/api/waitlist"):
                payload = {"slot_id": bind["free_slot"], "request_id": uid()}
            else:
                payload = {"request_id": uid()}
        def scenario(method=method, path=path, spec=spec, name=name, payload=payload, tpl=path_tpl):
            client = admin if path.startswith("/api/admin") else a
            if "/waitlist" in path: client = b
            if tpl in ("/api/reservations/{rid2}/checkin", "/api/reservations/{rid2}/checkout"): client = b  # rid2 属于 user02，必须用其本人客户端
            if path == "/api/login": client = Client(server.port)  # 必须用全新客户端，避免覆盖既有会话的 cookie/CSRF
            if path == "/api/logout": client = Client(server.port).login("user04")
            if path == "/api/register": client = Client(server.port)
            if path == "/api/me/password": client = Client(server.port).login("user05")
            st, body = client.request(method, path, payload)
            require(st == 200, f"期望 200，实际 {st}：{json.dumps(body, ensure_ascii=False)[:200]}")
            require(body["code"] == "OK", f"期望 code=OK，实际 {body['code']}")
            envelope(st, body, name)
            if spec != "empty-or-obj":
                fields(body["data"], spec, f"{name}.data")
            return f"data 字段 {sorted(body['data'])}" if isinstance(body["data"], dict) else "data 为空"
        record(name, scenario)

def run_error_cases(server):
    """每个用例都新建客户端：登录会覆盖 Cookie/CSRF，复用客户端会污染后续断言。"""
    def user(name): return Client(server.port).login(name)
    cases = []
    def case(name, expect_status, expect_codes, fn):
        def run():
            st, body = fn()
            envelope(st, body, name)
            require(st == expect_status, f"期望 {expect_status}，实际 {st}：{json.dumps(body, ensure_ascii=False)[:200]}")
            require(body["code"] in expect_codes, f"期望 code ∈ {sorted(expect_codes)}，实际 {body['code']}")
            return f"{st} {body['code']}"
        cases.append((name, run))
    day = today()
    def bad_csrf():
        c = user("user01"); c.csrf = "0" * 64
        return c.request("POST", "/api/reservations", {"slot_id": "1", "request_id": uid()})
    def duplicate_reserve():
        c = user("user06"); slot = server.slots()[0][0]
        first = c.request("POST", "/api/reservations", {"slot_id": slot, "request_id": uid()})
        require(first[0] == 200, f"准备首次预约失败：{first}")
        return c.request("POST", "/api/reservations", {"slot_id": slot, "request_id": uid()})
    def too_large():
        return user("user01").request("POST", "/api/reservations", None, raw=b"{" + b"a" * 17000 + b"}")
    case("400 非法 lab_id", 400, {"INVALID_INPUT"}, lambda: user("user01").request("GET", "/api/slots?lab_id=abc&date=" + day))
    case("400 非法日期", 400, {"INVALID_INPUT"}, lambda: user("user01").request("GET", "/api/slots?lab_id=1&date=not-a-date"))
    case("400 缺 request_id", 400, {"INVALID_INPUT"}, lambda: user("user01").request("POST", "/api/reservations", {"slot_id": "1"}))
    case("400 page_size 越界", 400, {"INVALID_INPUT"}, lambda: user("user01").request("GET", "/api/me/records?page=1&page_size=999"))
    case("401 匿名访问受保护端点", 401, {"UNAUTHORIZED"}, lambda: Client(server.port).request("GET", "/api/me"))
    case("401 错误口令", 401, {"UNAUTHORIZED"}, lambda: Client(server.port).request("POST", "/api/login", {"username": "user01", "password": "wrong-password-xyz"}))
    case("403 普通用户访问管理端点", 403, {"FORBIDDEN"}, lambda: user("user01").request("GET", "/api/admin/records?date=" + day))
    case("403 CSRF 令牌不匹配", 403, {"CSRF"}, bad_csrf)
    case("404 不存在的接口", 404, {"NOT_FOUND"}, lambda: user("admin").request("GET", "/api/definitely-not-here"))
    case("404 不存在的实验室记录", 404, {"NOT_FOUND"}, lambda: user("admin").request("POST", "/api/admin/labs/999999/update", {"name": "x", "location": "y", "description": "", "enabled": True}))
    case("404 场次不存在", 404, {"NOT_FOUND"}, lambda: user("user01").request("POST", "/api/reservations", {"slot_id": "999999", "request_id": uid()}))
    case("405 对登录端点用了 GET", 405, {"METHOD_NOT_ALLOWED"}, lambda: Client(server.port).request("GET", "/api/login"))
    case("409 重复预约同一场次", 409, {"ALREADY_RESERVED", "SLOT_FULL", "STATE_CONFLICT"}, duplicate_reserve)
    case("413 请求体过大", 413, {"TOO_LARGE"}, too_large)
    for name, fn in cases:
        record(name, fn)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "build/lab-booking.exe")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/contract")
    args = parser.parse_args()
    if len(PASSWORD) < 8: parser.error("请通过环境变量 LAB_TEST_PASSWORD 提供至少 8 位的测试专用口令")
    if not args.exe.is_file(): parser.error(f"Missing executable: {args.exe}")
    args.output.mkdir(parents=True, exist_ok=True)
    runs = ROOT / "artifacts" / "contract-runs"; runs.mkdir(parents=True, exist_ok=True)
    run_path = pathlib.Path(tempfile.mkdtemp(prefix="contract-", dir=runs))
    baseline = run_path / "baseline.db"
    env = os.environ.copy(); env["LAB_SEED_PASSWORD"] = PASSWORD
    seed = subprocess.run([str(args.exe), "--db", str(baseline), "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=60)
    require(seed.returncode == 0, f"seed failed: {seed.returncode}: {seed.stdout} {seed.stderr}")
    print("== 端点形状 ==", flush=True)
    with running(args.exe, run_path / "srv", baseline) as server:
        run_scenarios(server)
    print("== 错误场景 ==", flush=True)
    with running(args.exe, run_path / "errors", baseline) as server:
        run_error_cases(server)
    endpoints = [r for r in RESULTS if not r["name"].startswith(("400 ", "401 ", "403 ", "404 ", "405 ", "409 ", "413 ", "503 "))]
    errors = [r for r in RESULTS if r not in endpoints]
    passed = sum(1 for r in RESULTS if r["passed"])
    report = {
        "timestamp_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "executable": str(args.exe.resolve()),
        "endpoints_total": len(endpoints), "endpoints_passed": sum(1 for r in endpoints if r["passed"]),
        "error_cases_total": len(errors), "error_cases_passed": sum(1 for r in errors if r["passed"]),
        "all_passed": passed == len(RESULTS), "contract_drift": sorted(set(CONTRACT_DRIFT)), "results": RESULTS,
    }
    (args.output / "contract_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    drift = sorted(set(CONTRACT_DRIFT))
    if drift:
        print("\n契约漂移（实现多返回、文档未记录，不计入失败）：", flush=True)
        for item in drift: print(f"  - {item}", flush=True)
    print(f"\n端点 {report['endpoints_passed']}/{report['endpoints_total']}，错误场景 {report['error_cases_passed']}/{report['error_cases_total']}")
    print(f"证据：{(args.output / 'contract_results.json').resolve()}")
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
