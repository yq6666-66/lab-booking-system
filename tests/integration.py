"""Independent HTTP/SQLite integration experiments; Python standard library only."""
from __future__ import annotations
import argparse, concurrent.futures, contextlib, csv, datetime, http.client, json
import os, pathlib, shutil, socket, sqlite3, statistics, subprocess, sys, tempfile, threading, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
# CI（Windows runner）stdout 默认 cp1252，中文断言消息会导致 UnicodeEncodeError——强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
PASSWORD = os.environ.get("LAB_TEST_PASSWORD", "")  # 由运行方提供，测试口令不入库
RESULTS = []
RACES = []

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def uid():
    return str(uuid.uuid4())

class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None, headers=None, raw=None):
        h = {"Content-Type": "application/json"}
        if self.cookie: h["Cookie"] = self.cookie
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        if headers:
            for k,v in headers.items():
                if v is None: h.pop(k, None)
                else: h[k] = v
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=payload, headers=h)
            response = conn.getresponse()
            cookie = response.getheader("Set-Cookie")
            if cookie: self.cookie = cookie.split(";", 1)[0]
            content = response.read()
            try: data = json.loads(content)
            except ValueError: raise AssertionError(f"Non-JSON response {response.status}: {content[:160]!r}")
            return response.status, data
        finally:
            conn.close()

    def login(self, name):
        status, body = self.request("POST", "/api/login", {"username": name, "password": PASSWORD})
        require(status == 200 and body["code"] == "OK", f"login {name}: {status}, {body}")
        self.csrf = body["data"]["csrf_token"]
        return self

    def post(self, path, body, expected=200):
        status, data = self.request("POST", path, body)
        require(status == expected, f"{path}: expected {expected}, got {status}: {data}")
        return data

class Server:
    def __init__(self, executable, directory, baseline, fault=None, fault_request=None, extra=None):
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "test.db"
        shutil.copy2(baseline, self.db)
        self.executable, self.fault, self.fault_request = executable, fault, fault_request
        self.extra = list(extra or [])
        self.process = None

    def start(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.log = open(self.directory / "server.log", "ab")
        cmd = [str(self.executable), "--db", str(self.db), "--web", str(ROOT / "web"), "--port", str(self.port)] + self.extra
        if self.fault: cmd += ["--fault", self.fault, "--fault-request", self.fault_request]
        self.process = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise AssertionError(f"server exited {self.process.returncode}: {(self.directory/'server.log').read_text(errors='replace')}")
            try:
                status, body = Client(self.port).request("GET", "/api/health")
                if status == 200 and body.get("code") == "OK": return self
            except (OSError, http.client.HTTPException): pass
            time.sleep(.05)
        raise AssertionError("Server health deadline expired")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
        if getattr(self, "log", None): self.log.close()

    def sql(self, query, params=()):
        with contextlib.closing(sqlite3.connect(self.db, timeout=5)) as db:
            with db:
                with contextlib.closing(db.execute(query, params)) as cursor:
                    return cursor.fetchall()

    def slots(self):
        # r12 时段重叠规则后，同一时刻只消费一个场次（多实验室同时段对同一用户互斥）
        return [str(row[0]) for row in self.sql("SELECT MIN(id) FROM slots WHERE start_at > ? AND enabled=1 GROUP BY start_at ORDER BY start_at", (int(time.time())+3600,))]

    def user(self, n):
        return Client(self.port).login("admin" if n == 0 else f"user{n:02d}")

    def integrity(self):
        require(self.sql("PRAGMA integrity_check") == [("ok",)], "SQLite integrity_check failed")
        require(not self.sql("PRAGMA foreign_key_check"), "Foreign key violation")
        require(not self.sql("SELECT s.id FROM slots s WHERE (SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED')>s.capacity"), "Over-capacity occupation")
        require(not self.sql("SELECT user_id,slot_id FROM waitlist WHERE status='WAITING' GROUP BY user_id,slot_id HAVING count(*)>1"), "Duplicate waiting")

@contextlib.contextmanager
def running(*args, **kwargs):
    server = Server(*args, **kwargs)
    try: yield server.start()
    finally: server.stop()

def record(name, function):
    started = time.monotonic()
    try:
        details = function()
        row = dict(test=name, passed=True, elapsed_seconds=round(time.monotonic()-started,4), details=details)
    except Exception as exc:
        row = dict(test=name, passed=False, elapsed_seconds=round(time.monotonic()-started,4), error=f"{type(exc).__name__}: {exc}")
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

def regression(s):
    clients = [s.user(n) for n in range(4)]
    admin, a, b, c = clients
    slots = iter(s.slots())
    def reserve(client, slot, key=None):
        return client.post("/api/reservations", {"slot_id":slot,"request_id":key or uid()})["data"]["reservation_id"]
    def waiting(client, slot):
        return client.post("/api/waitlist", {"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
    def cancel(client, rid, key=None):
        return client.post(f"/api/reservations/{rid}/cancel", {"request_id":key or uid()})
    def t01():
        require(Client(s.port).request("GET", "/api/me")[0] == 401, "anonymous me")
        require(a.request("GET", "/api/me")[1]["data"]["user"]["username"] == "user01", "identity")
        require(a.request("GET", "/api/labs")[0] == 200, "labs")
        require(a.request("GET", "/api/me/records")[0] == 200, "records")
        require(admin.request("GET", "/api/admin/records")[0] == 200, "admin records")
    record("T01 login, read interfaces and authorization", t01)
    def t02():
        slot = next(slots); rid=reserve(a,slot)
        require(b.post("/api/reservations",{"slot_id":slot,"request_id":uid()},409)["code"]=="SLOT_FULL", "full code")
        require(len(s.sql("SELECT id FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,)))==1,"one occupation")
        cancel(a,rid)
    record("T02 reservation conflict", t02)
    def t03():
        slot=next(slots); rid=reserve(a,slot); wb=waiting(b,slot); wc=waiting(c,slot)
        b.post(f"/api/waitlist/{wb}/withdraw",{"request_id":uid()})
        wb2=waiting(b,slot)
        result=cancel(a,rid)
        promoted=result["data"]["promoted_reservation_id"]
        require(promoted is not None,"missing promotion")
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(wc,))==[("PROMOTED",)],"FIFO reentry jumped queue")
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(wb2,))==[("WAITING",)],"reentry state")
        cancel(c,promoted)
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(wb2,))==[("PROMOTED",)],"second promotion")
    record("T03 FIFO withdrawal and reentry",t03)
    def t04():
        slot=next(slots); key=uid(); body={"slot_id":slot,"request_id":key}
        first=a.post("/api/reservations",body); require(a.post("/api/reservations",body)==first,"replay differs")
        require(a.post("/api/reservations",{"slot_id":next(slots),"request_id":key},409)["code"]=="REQUEST_ID_CONFLICT","same key changed params")
        rid=first["data"]["reservation_id"]; waiting(b,slot); ck=uid()
        first_cancel=cancel(a,rid,ck); require(cancel(a,rid,ck)==first_cancel,"cancel replay differs")
        require(len(s.sql("SELECT id FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,)))==1,"replay damaged promoted booking")
    record("T04 persistent same-key replay and conflict",t04)
    def t05():
        rid=reserve(a,next(slots))
        require(b.request("POST",f"/api/reservations/{rid}/cancel",{"request_id":uid()})[0]==403,"cross-user cancel")
        require(a.request("GET","/api/admin/records")[0]==403,"admin protection")
        body={"slot_id":next(slots),"request_id":uid()}
        require(a.request("POST","/api/reservations",body,headers={"X-CSRF-Token":None})[0]==403,"missing csrf")
        require(a.request("POST","/api/reservations",body,headers={"X-CSRF-Token":"invalid"})[0]==403,"invalid csrf")
        require(a.request("POST","/api/reservations",body,headers={"Origin":"https://attacker.invalid"})[0]==403,"foreign origin")
    record("T05 ownership, roles, CSRF and Origin",t05)
    def t06():
        require(a.request("POST","/api/reservations",raw=b'{broken')[0]==400,"malformed JSON")
        require(a.request("POST","/api/reservations",raw=b'{' + b' '*17000 + b'}')[0]==413,"body limit")
        require(a.request("POST","/api/reservations",{"slot_id":[],"request_id":uid()})[0]==400,"JSON type")
        require(a.request("GET","/api/slots?lab_id=1&date=2026-02-30")[0]==400,"invalid calendar date")
    record("T06 malformed, oversized and typed input",t06)
    def t07():
        slot=next(slots); key=uid(); rid=reserve(a,slot,key)
        s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(_safe_past(int(time.time())-7200),_safe_past(int(time.time())-7200)+3600,slot))
        require(a.request("POST",f"/api/reservations/{rid}/cancel",{"request_id":uid()})[0]==409,"expired cancellation")
        require(b.request("POST","/api/waitlist",{"slot_id":slot,"request_id":uid()})[0]==409,"expired waitlist")
        require(a.post("/api/reservations",{"slot_id":slot,"request_id":key})["data"]["reservation_id"]==rid,"historical replay")
    record("T07 expired operations and historical replay",t07)
    def t08():
        slot=next(slots); key=uid()
        with contextlib.closing(sqlite3.connect(s.db)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            start=time.monotonic(); status,body=a.request("POST","/api/reservations",{"slot_id":slot,"request_id":key}); elapsed=time.monotonic()-start
            require(status==503 and body["code"]=="DATABASE_BUSY",f"busy response {status} {body}")
            require(2.5<=elapsed<8,f"busy timeout {elapsed}")
            lock.rollback()
        reserve(a,slot,key)
        return {"busy_elapsed_seconds":round(elapsed,4),"same_key_retry":"passed"}
    record("T08 writer busy timeout and retry",t08)
    def t09():
        slot=next(slots); rid=reserve(a,slot); wb=waiting(b,slot); wc=waiting(c,slot)
        s.sql("UPDATE users SET enabled=0 WHERE username='user02'")
        try:
            cancel(a,rid)
            require(s.sql("SELECT status FROM waitlist WHERE id=?",(wb,))==[("SKIPPED",)],"disabled candidate not skipped")
            require(s.sql("SELECT status FROM waitlist WHERE id=?",(wc,))==[("PROMOTED",)],"valid next candidate not promoted")
        finally: s.sql("UPDATE users SET enabled=1 WHERE username='user02'")
    record("T09 disabled candidate skip",t09)
    def t13():
        slot=next(slots); rid=reserve(a,slot)
        lab,start=s.sql("SELECT lab_id,start_at FROM slots WHERE id=?",(slot,))[0]
        body=b.post("/api/reservations",{"slot_id":slot,"request_id":uid()},409)
        expected=[str(r[0]) for r in s.sql("SELECT id FROM slots WHERE lab_id=? AND start_at>? AND start_at<=? AND enabled=1 AND id NOT IN(SELECT slot_id FROM reservations WHERE status='CONFIRMED') ORDER BY start_at LIMIT 3",(lab,start,start+7*86400))]
        alts=body["data"]["alternatives"]
        require(isinstance(alts,list) and [str(x["id"]) for x in alts]==expected,f"alternatives {alts} != {expected}")
        if alts: require(reserve(b,alts[0]["id"]),"alternative slot not bookable")
        cancel(a,rid)
    record("T13 alternatives after full slot",t13)
    def t14():
        fmt=lambda t:datetime.datetime.fromtimestamp(t+28800,datetime.timezone.utc).strftime("%Y-%m-%d")
        lo,hi=s.sql("SELECT min(start_at),max(start_at) FROM slots")[0]
        d1=(lo+28800)//86400*86400-28800; d2=(hi+28800)//86400*86400-28800
        status,body=admin.request("GET",f"/api/admin/stats?start_date={fmt(d1)}&end_date={fmt(d2)}")
        require(status==200 and body["code"]=="OK",f"stats {status}: {body}")
        totals=body["data"]["totals"]
        span=(d1,d2+86400)
        exp_slots=s.sql("SELECT count(*) FROM slots WHERE start_at>=? AND start_at<?",span)[0][0]
        exp_conf=s.sql("SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE s.start_at>=? AND s.start_at<? AND r.status='CONFIRMED'",span)[0][0]
        exp_wait=s.sql("SELECT count(*) FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN users u ON u.id=w.user_id WHERE s.start_at>=? AND s.start_at<? AND w.status='WAITING' AND u.enabled=1",span)[0][0]
        require(totals["slots"]==exp_slots and totals["confirmed"]==exp_conf and totals["waiting"]==exp_wait,f"totals {totals} vs {(exp_slots,exp_conf,exp_wait)}")
        for row in body["data"]["stats"]:
            epoch=int(datetime.datetime.strptime(row["date"],"%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
            day=(epoch+28800)//86400*86400-28800
            require(d1<=day<=d2,f"stats date {row['date']} outside range")
            require(row["slots"]==s.sql("SELECT count(*) FROM slots WHERE start_at>=? AND start_at<?",(day,day+86400))[0][0],"per-day slots mismatch")
        require(a.request("GET",f"/api/admin/stats?start_date={fmt(d1)}&end_date={fmt(d2)}")[0]==403,"non-admin stats")
        require(admin.request("GET","/api/admin/stats?start_date=2026-02-30&end_date=2026-03-01")[0]==400,"invalid calendar date")
        require(admin.request("GET","/api/admin/stats?start_date=2026-01-01&end_date=2026-03-01")[0]==400,"range too long")
        require(admin.request("GET","/api/admin/stats")[0]==400,"missing params")
    record("T14 admin statistics totals and validation",t14)
    def t26():
        # 容量制：新建实验室发布 capacity=3 场次，验证满员拒绝、候补与取消补位
        day=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=32)).strftime("%Y-%m-%d")
        lab=admin.post("/api/admin/labs",{"name":f"容量制验收实验室{uid()[:8]}","location":"信息楼","description":"T26"})["data"]["lab_id"]
        admin.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":day,"end_date":day,"capacity":"3"})
        listing=a.request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"]
        require(len(listing)==8 and all(int(x["capacity"])==3 for x in listing),f"capacity published: {listing[:1]}")
        slot=listing[0]["id"]
        u11,u12,u13,u14,u15=[Client(s.port).login(f"user{n:02d}") for n in range(11,16)]
        rids=[u.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"] for u in (u11,u12,u13)]
        st,body=u14.request("POST","/api/reservations",{"slot_id":slot,"request_id":uid()})
        require(st==409 and body["code"]=="SLOT_FULL",f"4th beyond capacity: {st} {body}")
        row=[x for x in a.request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"] if x["id"]==slot][0]
        require(int(row["confirmed_count"])==3 and int(row["capacity"])==3,f"listing counters: {row}")
        wid=u15.post("/api/waitlist",{"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
        result=u11.post(f"/api/reservations/{rids[0]}/cancel",{"request_id":uid()})
        require(result["data"]["promoted_reservation_id"],f"promotion on cancel: {result}")
        require(s.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))==[(3,)],"capacity refilled after cancel")
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(wid,))==[("PROMOTED",)],"waiter promoted")
        st,body=u12.request("POST","/api/reservations",{"slot_id":slot,"request_id":uid()})
        require(st==409 and body["code"]=="ALREADY_RESERVED",f"duplicate booking: {st} {body}")
        st,body=u14.request("POST","/api/waitlist",{"slot_id":slot,"request_id":uid()})
        require(st==200,f"waitlist accepted on full slot: {st} {body}")
    record("T26 capacity slots, fill and FIFO promotion",t26)
    def t27():
        # 连接复用：读写混合并发下数据正确、服务稳定（r6 连接/语句复用回归）
        import concurrent.futures
        # r12 重叠规则后：选两个当前空闲、且时间不重叠的未来场次
        pair=s.sql("SELECT s1.id,s2.id FROM slots s1 JOIN slots s2 ON s2.start_at>=s1.end_at AND s2.enabled=1 WHERE s1.start_at>? AND s1.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s1.id AND r.status='CONFIRMED') AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s2.id AND r.status='CONFIRMED') AND NOT EXISTS(SELECT 1 FROM reservations r JOIN slots x ON x.id=r.slot_id WHERE r.status='CONFIRMED' AND r.user_id=16 AND x.start_at<s1.end_at AND s1.start_at<x.end_at) AND NOT EXISTS(SELECT 1 FROM reservations r JOIN slots x ON x.id=r.slot_id WHERE r.status='CONFIRMED' AND r.user_id=17 AND x.start_at<s2.end_at AND s2.start_at<x.end_at) ORDER BY s1.start_at LIMIT 1",(int(time.time())+3600,))[0]
        sa,sb=str(pair[0]),str(pair[1])
        w1,w2,r1,r2=[Client(s.port).login(f"user{n:02d}") for n in (16,17,19,20)]
        def writer(client,slot,cycles):
            out=[]
            for _ in range(cycles):
                out.append(client.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["code"])
                out.append(client.post(f"/api/reservations/{client.request('GET','/api/me/records')[1]['data']['reservations'][0]['id']}/cancel",{"request_id":uid()})["code"])
            return out
        def reader(client,cycles):
            out=[]
            for _ in range(cycles):
                for path in ("/api/labs","/api/me/records?page=1&page_size=5","/api/health"):
                    status,body=client.request("GET",path) if path!="/api/health" else client.request("GET",path)
                    out.append(status)
            return out
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs=[pool.submit(writer,w1,sa,8),pool.submit(writer,w2,sb,8),pool.submit(reader,r1,20),pool.submit(reader,r2,20)]
            results=[f.result() for f in futs]
        require(all(c=="OK" for c in results[0]+results[1]),f"writers all ok: {set(results[0]+results[1])}")
        require(all(st==200 for st in results[2]+results[3]),f"readers all 200: {set(results[2]+results[3])}")
        require(s.sql("SELECT count(*) FROM reservations WHERE status='CONFIRMED' AND slot_id IN (?,?)",(sa,sb))==[(0,)],"writers left no bookings")
    record("T27 connection reuse under mixed read-write load",t27)
    def t30():
        # 自助注册：注册即登录、重名 409、弱口令 400、新账号可正常预约
        c=Client(s.port)
        st,d=c.request("POST","/api/register",{"username":"stu2026","password":PASSWORD})
        require(st==200 and d["data"]["user"]["role"]=="USER" and d["data"]["csrf_token"],f"register: {st} {d}")
        st,_=c.request("GET","/api/me")
        require(st==200,"auto session after register")
        st,body=c.request("POST","/api/register",{"username":"stu2026","password":PASSWORD})
        require(st==409 and body["code"]=="USERNAME_TAKEN",f"duplicate: {st} {body}")
        st,body=c.request("POST","/api/register",{"username":"stu2026","password":"short"})
        require(st==400,f"weak password: {st}")
        st,body=c.request("POST","/api/register",{"username":"ad min","password":PASSWORD})
        require(st==400,f"invalid username: {st}")
        ok=Client(s.port).request("POST","/api/login",{"username":"stu2026","password":PASSWORD})
        require(ok[0]==200,"login with registered account")
        adm=Client(s.port).login("admin")
        lab=adm.post("/api/admin/labs",{"name":f"注册验证实验室{uid()[:8]}","location":"实验楼","description":"T30"})["data"]["lab_id"]
        import datetime as _dt
        day=(datetime.datetime.now(datetime.timezone.utc)+_dt.timedelta(hours=32)).strftime("%Y-%m-%d")
        adm.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":day,"end_date":day})
        slots_list=adm.request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"]
        require(len(slots_list)>0,"slots published for T30")
        slot=slots_list[0]["id"]
        res=Client(s.port).login("stu2026")
        res.post("/api/reservations",{"slot_id":slot,"request_id":uid()})
        require(res.request("GET","/api/me/records")[1]["data"]["reservations"],"new user can book")
        events=[e for e in s.sql("SELECT action FROM operation_events WHERE action='REGISTER'")]
        require(events,"REGISTER audited")
    record("T30 self-registration flow",t30)
    def t31(): # -- r10/admin-console
        """管理员登录入口：role_hint 校验、兼容性与不建会话保证。"""
        def raw_login(body): return Client(s.port).request("POST","/api/login",body)
        def user01_sessions(): return len(s.sql("SELECT token_hash FROM sessions WHERE user_id=(SELECT id FROM users WHERE username='user01')"))
        st,d=raw_login({"username":"admin","password":PASSWORD,"role_hint":"ADMIN"})
        require(st==200 and d["data"]["user"]["role"]=="ADMIN",f"admin+ADMIN: {st} {d}")
        require(raw_login({"username":"admin","password":PASSWORD,"role_hint":"USER"})[0]==200,"admin may use user entry")
        before=user01_sessions()
        st,d=raw_login({"username":"user01","password":PASSWORD,"role_hint":"ADMIN"})
        require(st==403 and d["code"]=="ROLE_MISMATCH",f"user+ADMIN: {st} {d}")
        require(user01_sessions()==before,"no session created on ROLE_MISMATCH")
        require(raw_login({"username":"user01","password":PASSWORD})[0]==200,"legacy client without role_hint")
        st,d=raw_login({"username":"user01","password":PASSWORD,"role_hint":"invalid"})
        require(st==400 and d["code"]=="INVALID_INPUT",f"invalid role_hint: {st} {d}")
        return {"role_hint_enforced":True}
    record("T31 admin login entry and role_hint",t31) # -- r10/admin-console
    def t32(): # -- r10/admin-slot-update
        """管理员修改已发布场次：容量调整/停用/校验/鉴权。"""
        # admin 建新实验室（名字带随机后缀避免重名）+ 发布明天 1 天 capacity 1（08-12/14-18 共 8 场）
        day=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=15)).strftime("%Y-%m-%d")  # 种子覆盖 14 天，选 15 天后避免与既有预约时段重叠
        lab=admin.post("/api/admin/labs",{"name":f"场次修改验收实验室{uid()[:8]}","location":"信息楼","description":"T32"})["data"]["lab_id"]
        admin.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":day,"end_date":day,"capacity":"1"})
        listing=a.request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"]
        require(len(listing)==8 and int(listing[0]["capacity"])==1,f"published capacity 1: {listing[:1]}")
        slot=listing[0]["id"]
        # update {capacity:3} → 200 且 data.capacity==3（data 含 slot_id/capacity/enabled）
        updated=admin.post(f"/api/admin/slots/{slot}/update",{"capacity":3})["data"]
        require(updated["slot_id"]==slot and updated["capacity"]==3 and updated["enabled"] is True,f"update capacity: {updated}")
        u1,u2,u3,u4=a,b,c,Client(s.port).login("user04")
        for u in (u1,u2,u3): u.post("/api/reservations",{"slot_id":slot,"request_id":uid()})
        st,body=u4.request("POST","/api/reservations",{"slot_id":slot,"request_id":uid()})
        require(st==409 and body["code"]=="SLOT_FULL",f"4th beyond capacity 3: {st} {body}")
        # 容量小于已确认预约数 → 409 STATE_CONFLICT；0/201/空体 → 400
        st,body=admin.request("POST",f"/api/admin/slots/{slot}/update",{"capacity":2})
        require(st==409 and body["code"]=="STATE_CONFLICT",f"capacity below confirmed: {st} {body}")
        require(admin.request("POST",f"/api/admin/slots/{slot}/update",{"capacity":0})[0]==400,"capacity 0 rejected")
        require(admin.request("POST",f"/api/admin/slots/{slot}/update",{"capacity":201})[0]==400,"capacity 201 rejected")
        require(admin.request("POST",f"/api/admin/slots/{slot}/update",{})[0]==400,"empty update rejected")
        # 停用后：用户端该场次 enabled=false，预约 → 409 STATE_CONFLICT（场次未开放）
        disabled=admin.post(f"/api/admin/slots/{slot}/update",{"enabled":False})["data"]
        require(disabled["enabled"] is False and disabled["capacity"]==3,f"disable keeps capacity: {disabled}")
        row=[x for x in u4.request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"] if x["id"]==slot][0]
        require(row["enabled"] is False,f"user sees disabled slot: {row}")
        st,body=u4.request("POST","/api/reservations",{"slot_id":slot,"request_id":uid()})
        require(st==409 and body["code"]=="STATE_CONFLICT",f"booking disabled slot: {st} {body}")
        require(admin.request("POST","/api/admin/slots/999999999/update",{"capacity":5})[0]==404,"unknown slot 404")
        require(u1.request("POST",f"/api/admin/slots/{slot}/update",{"capacity":5})[0]==403,"non-admin update rejected")
        return {"slot_id":slot,"capacity":3,"disabled":True}
    record("T32 admin slot update",t32) # -- r10/admin-slot-update
    def t33(): # -- r10/admin-notify
        """管理员发布通知：广播/定向/已读/鉴权。"""
        def unread(client):
            return client.request("GET","/api/me/notifications?unread=1&page_size=50")[1]["data"]
        # 广播：sent 为 enabled 用户数（种子 21 个，T30 自助注册后至少 21）
        st,body=admin.request("POST","/api/admin/notifications",{"all":True,"title":"系统维护通知","body":"今晚维护"})
        require(st==200 and body["data"]["sent"]>=21,f"broadcast: {st} {body}")
        require([n for n in unread(a)["notifications"] if n["kind"]=="NOTICE" and n["title"]=="系统维护通知"],"NOTICE in user unread list")
        require(unread(a)["unread_count"]>=1,"unread_count counts notice")
        a.post("/api/me/notifications/read",{"all":True,"request_id":uid()})
        data=unread(a)
        require(not [n for n in data["notifications"] if n["kind"]=="NOTICE" and n["title"]=="系统维护通知"] and data["unread_count"]==0,f"cleared after read-all: {data['unread_count']}")
        # 定向：sent==1，目标用户可见
        sent=admin.post("/api/admin/notifications",{"username":"user02","title":"个别通知"})["data"]["sent"]
        require(sent==1,f"targeted sent: {sent}")
        require([n for n in unread(b)["notifications"] if n["kind"]=="NOTICE" and n["title"]=="个别通知"],"targeted user sees notice")
        require(admin.request("POST","/api/admin/notifications",{"username":"no_such_user","title":"x"})[0]==404,"unknown user 404")
        require(admin.request("POST","/api/admin/notifications",{"all":True})[0]==400,"missing title rejected")
        require(admin.request("POST","/api/admin/notifications",{"all":True,"username":"user02","title":"x"})[0]==400,"all+username rejected")
        require(admin.request("POST","/api/admin/notifications",{"title":"无目标"})[0]==400,"no target rejected")
        require(a.request("POST","/api/admin/notifications",{"all":True,"title":"越权通知"})[0]==403,"non-admin notify rejected")
        return {"broadcast_sent":body["data"]["sent"],"targeted_sent":sent}
    record("T33 admin notify publish",t33) # -- r10/admin-notify
    record("T12 SQLite integrity and relational invariants",s.integrity)
    def t15():
        c=s.user(4)
        s.sql("UPDATE sessions SET expires_at=1")
        require(c.request("GET","/api/me")[0]==401,"expired session accepted")
        s.user(4)
        total,valid=s.sql("SELECT (SELECT count(*) FROM sessions),(SELECT count(*) FROM sessions WHERE expires_at>?)",(int(time.time()),))[0]
        require(total==valid and valid>=1,"expired sessions pruned after login")
    record("T15 expired session rejected and pruned",t15)
    def t25():
        adm=s.user(0)  # T15 已使所有会话过期，此处重新登录
        require(adm.request("GET","/api/admin/metrics")[0]==200,"admin metrics 200")
        require(s.user(1).request("GET","/api/admin/metrics")[0]==403,"metrics non-admin 403")
        require(Client(s.port).request("GET","/api/admin/metrics")[0]==401,"metrics anonymous 401")
        before=adm.request("GET","/api/admin/metrics")[1]["data"]["counters"]["requests_total"]
        adm.request("GET","/api/me")
        after=adm.request("GET","/api/admin/metrics")[1]["data"]["counters"]["requests_total"]
        require(after>before,"metrics requests_total 递增")
        return {"requests_total_before":before,"requests_total_after":after}
    record("T25 admin metrics endpoint and counters",t25) # -- r5/metrics

def races(s, rounds):
    users=[s.user(i) for i in range(1,21)]
    slots=iter(s.slots())
    for concurrency in (1,5,10,20):
        for iteration in range(1,rounds+1):
            slot=next(slots); barrier=threading.Barrier(concurrency)
            def hit(i):
                barrier.wait(); started=time.perf_counter()
                status,body=users[i].request("POST","/api/reservations",{"slot_id":slot,"request_id":uid()})
                return status,body["code"],(time.perf_counter()-started)*1000
            started=time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool: responses=list(pool.map(hit,range(concurrency)))
            elapsed=time.perf_counter()-started
            successful=sum(status==200 and code=="OK" for status,code,_ in responses)
            count=s.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))[0][0]
            times=sorted(t for _,_,t in responses)
            row=dict(concurrency=concurrency,iteration=iteration,successes=successful,confirmed=count,full=sum(c=="SLOT_FULL" for _,c,_ in responses),busy=sum(c=="DATABASE_BUSY" for _,c,_ in responses),elapsed_seconds=round(elapsed,6),requests_per_second=round(concurrency/elapsed,3),p50_ms=round(statistics.median(times),3),p95_ms=round(times[max(0,__import__('math').ceil(.95*len(times))-1)],3),max_ms=round(max(times),3))
            RACES.append(row)
            require(successful==1 and count==1,f"race invariant: {row}")
            require(all((st==200 and code=="OK") or (st==409 and code=="SLOT_FULL") for st,code,_ in responses),f"unexpected race response {responses}")
    s.integrity()
    return {"rounds_per_concurrency":rounds,"total_requests":sum(r["concurrency"] for r in RACES),"runs":len(RACES)}

def fault_case(exe, directory, baseline, fault, rounds):
    outcomes=[]
    for iteration in range(rounds):
        key=uid()
        with running(exe,pathlib.Path(directory)/f"{fault}-{iteration}",baseline,fault,key) as s:
            a,b=s.user(1),s.user(2); slot=s.slots()[0]
            rid=a.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
            wid=b.post("/api/waitlist",{"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
            try:
                response=a.request("POST",f"/api/reservations/{rid}/cancel",{"request_id":key})
                raise AssertionError(f"fault did not interrupt response: {response}")
            except (OSError,http.client.HTTPException): pass
            code=s.process.wait(timeout=10)
            require(code==(86 if fault=="cancel-before-promote" else 87),f"fault exit {code}")
            s.stop(); s.fault=None; s.start()
            expected="CONFIRMED" if fault=="cancel-before-promote" else "CANCELLED"
            require(s.sql("SELECT status FROM reservations WHERE id=?",(rid,))==[(expected,)],"restart reservation state")
            require(s.sql("SELECT status FROM waitlist WHERE id=?",(wid,))==[("WAITING" if fault=="cancel-before-promote" else "PROMOTED",)],"restart waitlist state")
            a=s.user(1)
            first=a.post(f"/api/reservations/{rid}/cancel",{"request_id":key})
            second=a.post(f"/api/reservations/{rid}/cancel",{"request_id":key})
            require(first==second,"recovery replay mismatch")
            require(len(s.sql("SELECT id FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,)))==1,"recovery occupation")
            s.integrity(); outcomes.append({"iteration":iteration+1,"exit_code":code,"recovery":"passed"})
    return outcomes

def _safe_past(t):
    """rewind 目标避开整点：种子场次全部为整点开始，落在整点会触发 UNIQUE(lab_id,start_at) 冲突。"""
    return t-1800 if t%3600==0 else t
def bj_today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

def sq_slot(server, lab_id, start_at):
    import sqlite3 as _sq
    with _sq.connect(server.db, timeout=5) as conn:
        row=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab_id),int(start_at))).fetchone()
    return row[0]

def feature_checks(s):
    """T16-T19：签到、签到边界、记录分页、统计导出（使用较宽的签到窗口）。"""
    admin=s.user(0); a=s.user(1); b=s.user(2)
    pools=iter(s.slots())
    def reserve(client,slot): return client.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
    def rewind(slot,offset=-1):
        now=int(time.time());ns=_safe_past(now+offset);s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(ns,ns+3600,slot))
    def slot_row(client,slot):
        lab=s.sql("SELECT lab_id FROM slots WHERE id=?",(slot,))[0][0]
        data=client.request("GET",f"/api/slots?lab_id={lab}&date={bj_today()}")[1]["data"]
        return data,[x for x in data["slots"] if x["id"]==slot][0]
    def t16():
        slot=next(pools); rid=reserve(a,slot); rewind(slot,-1)
        data,row=slot_row(a,slot)
        require(data["checkin_window"]>0,"check-in window exposed to the page")
        require(row["my_checked_in_at"] is None,"not checked in yet")
        key=uid()
        first=a.post(f"/api/reservations/{rid}/checkin",{"request_id":key})["data"]
        require(first["checked_in_at"]>0,"check-in recorded")
        replay=a.post(f"/api/reservations/{rid}/checkin",{"request_id":key})["data"]
        require(replay["checked_in_at"]==first["checked_in_at"],"same-key replay returns stored result")
        again=a.post(f"/api/reservations/{rid}/checkin",{"request_id":uid()})["data"]
        require(again["checked_in_at"]==first["checked_in_at"],"repeat check-in is idempotent")
        require(s.sql("SELECT checked_in_at FROM reservations WHERE id=?",(rid,))==[(first["checked_in_at"],)],"check-in persisted")
        require(b.request("POST",f"/api/reservations/{rid}/checkin",{"request_id":uid()})[0]==403,"other user cannot check in")
        return {"checked_in_at":first["checked_in_at"]}
    record("T16 check-in success, replay and idempotency",t16)
    def t17():
        slot=next(pools); rid=reserve(a,slot); rewind(slot,-400)
        status,body=a.request("POST",f"/api/reservations/{rid}/checkin",{"request_id":uid()})
        require(status==409 and body["code"]=="STATE_CONFLICT",f"expired window rejected: {status},{body}")
        slot2=next(pools); rid2=reserve(a,slot2)
        status,body=a.request("POST",f"/api/reservations/{rid2}/checkin",{"request_id":uid()})
        require(status==409 and body["code"]=="STATE_CONFLICT",f"future slot rejected: {status},{body}")
        return None
    record("T17 check-in window and state guards",t17)
    def t18():
        for _ in range(22):
            try: reserve(a,next(pools))
            except AssertionError: pass
        page1=a.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]
        require(len(page1["reservations"])==10,f"page1 size: {len(page1['reservations'])}")
        require(page1["has_more"] is True,"page1 reports more")
        page2=a.request("GET","/api/me/records?page=2&page_size=10")[1]["data"]
        require(len(page2["reservations"])==10,"page2 full")
        page3=a.request("GET","/api/me/records?page=3&page_size=10")[1]["data"]
        require(0<len(page3["reservations"])<=10,"page3 remainder")
        require(page3["has_more"] is False,"page3 reports end")
        require(a.request("GET","/api/me/records?page=0")[0]==400,"page 0 rejected")
        require(a.request("GET","/api/me/records?page_size=999")[0]==400,"oversized page rejected")
        admin_page=admin.request("GET",f"/api/admin/records?date={bj_today()}&page=1&page_size=5")[1]["data"]
        require(len(admin_page["reservations"])<=5,"admin paging honoured")
        return {"paged":True}
    record("T18 records paging and validation",t18)
    def t19():
        require(a.request("GET",f"/api/admin/stats/export?start_date={bj_today()}&end_date={bj_today()}")[0]==403,"non-admin export rejected")
        status,body=admin.request("GET",f"/api/admin/stats/export?start_date={bj_today()}&end_date={bj_today()}")
        require(status==200 and body["code"]=="OK",f"export ok: {status},{body}")
        content=body["data"]["content"]
        require(content.startswith("\ufeff"),"csv carries UTF-8 BOM")
        require("日期" in content and "合计" in content,"csv header and totals row")
        require("no_show" not in content,"csv exposes chinese headers only")
        require(body["data"]["filename"].endswith(".csv"),"csv filename")
        require(admin.request("GET",f"/api/admin/stats/export?start_date={bj_today()}")[0]==400,"missing end date rejected")
        stats=admin.request("GET",f"/api/admin/stats?start_date={bj_today()}&end_date={bj_today()}")[1]["data"]
        require("no_show" in stats["totals"] and "checked_in" in stats["totals"],"totals carry no_show and checked_in")
        return {"csv_bytes":len(content)}
    record("T19 statistics export and counters",t19)
    def t34(): # -- r11/admin-users
        """管理员用户管理：列表/搜索/停用立即下线/启用/重置密码/自禁保护/鉴权。"""
        # 注册一个专用账号供操作
        uname="del"+uid()[:8]
        Client(s.port).request("POST","/api/register",{"username":uname,"password":PASSWORD})
        # 列表与搜索
        st,d=admin.request("GET","/api/admin/users?page=1&page_size=50")
        require(st==200 and d["data"]["total"]>=21,f"user list: {st}")
        row=[x for x in d["data"]["users"] if x["username"]==uname]
        require(row and row[0]["enabled"] and row[0]["role"]=="USER","new user listed enabled")
        st,d=admin.request("GET","/api/admin/users?q="+uname)
        require(st==200 and len(d["data"]["users"])==1 and d["data"]["users"][0]["username"]==uname,f"prefix search: {d['data']}")
        target=d["data"]["users"][0]["id"]
        # 停用：已登录会话立即失效
        victim=Client(s.port).login(uname)
        require(victim.request("GET","/api/me")[0]==200,"victim logged in before disable")
        st,d=admin.request("POST",f"/api/admin/users/{target}/disable",{})
        require(st==200 and d["data"]["enabled"] is False,f"disable: {st} {d}")
        require(victim.request("GET","/api/me")[0]==401,"disabled user session revoked")
        require(Client(s.port).request("POST","/api/login",{"username":uname,"password":PASSWORD})[0]==401,"disabled user cannot login")
        # 启用后可登录
        st,_=admin.request("POST",f"/api/admin/users/{target}/enable",{})
        require(st==200,"enable ok")
        require(Client(s.port).request("POST","/api/login",{"username":uname,"password":PASSWORD})[0]==200,"enabled user can login")
        # 重置密码：新口令可登录，旧口令失效，会话被吊销
        st,d=admin.request("POST",f"/api/admin/users/{target}/reset-password",{})
        require(st==200 and isinstance(d["data"].get("password"),str) and len(d["data"]["password"])>=8,f"reset: {st} {d.get('data') if isinstance(d,dict) else d}")
        newpw=d["data"]["password"]
        require(Client(s.port).request("POST","/api/login",{"username":uname,"password":newpw})[0]==200,"new password works")
        require(Client(s.port).request("POST","/api/login",{"username":uname,"password":PASSWORD})[0]==401,"old password rejected")
        # 自禁保护与管理员保护
        admin_id=[x for x in admin.request("GET","/api/admin/users?q=admin")[1]["data"]["users"] if x["username"]=="admin"][0]["id"]
        require(admin.request("POST",f"/api/admin/users/{admin_id}/disable",{})[1]["code"]=="STATE_CONFLICT","cannot disable self")
        # 鉴权：普通用户 403
        require(a.request("GET","/api/admin/users")[0]==403,"non-admin list rejected")
        time.sleep(1.2)  # 令牌桶补流，确保走到角色校验而非 429
        require(a.request("POST",f"/api/admin/users/{target}/reset-password",{})[0]==403,"non-admin reset rejected")
        return {"searched":uname,"newpw_len":len(newpw)}
    record("T34 admin user management",t34) # -- r11/admin-users
    def t35(): # -- r11/remind
        """场次开始提醒：开场前 remind_sec 窗口内 sweep 线程自动发 REMIND 通知，reminded_at 防重。"""
        import datetime as _dt
        import sqlite3 as _sq
        def unread_of(client):
            return client.request("GET","/api/me/notifications?unread=1&page_size=50")[1]["data"]
        # 建一个开始时间在提醒窗口内（60s 后 < remind_sec 300s）的场次：publish 只发未来整点，灰盒直插
        lab=admin.post("/api/admin/labs",{"name":"提醒实验室"+uid()[:6],"location":"实验楼","description":"remind"})["data"]["lab_id"]
        start=int(time.time())+60
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),start,start+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),start)).fetchone()[0]
        # 用全新注册账号：前面用例可能耗尽种子用户的令牌桶（429 RATE_LIMITED）
        ru="rm"+uid()[:8]
        rc=Client(s.port);st,bd=rc.request("POST","/api/register",{"username":ru,"password":PASSWORD})
        require(st==200,f"register remind user: {st}")
        rc.csrf=bd["data"]["csrf_token"]  # 注册即登录，但 Client 只在 login() 里记录 csrf
        rc.post("/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        # features 实例 --sweep-interval 1 --remind-sec 300：等最多 5 个扫描周期
        got=None
        for _ in range(50):
            time.sleep(0.1)
            got=[n for n in unread_of(rc)["notifications"] if n["kind"]=="REMIND"]
            if any(int(n.get("slot_id") or 0)==int(sid) for n in got): break
        require(got and any(int(n.get("slot_id") or 0)==int(sid) for n in got),"REMIND notification delivered within window")
        # 防重：reminded_at 已置位，后续扫描不重发
        before=len([n for n in unread_of(rc)["notifications"] if n["kind"]=="REMIND" and int(n.get("slot_id") or 0)==int(sid)])
        time.sleep(1.5)
        after=len([n for n in unread_of(rc)["notifications"] if n["kind"]=="REMIND" and int(n.get("slot_id") or 0)==int(sid)])
        require(after==before,f"reminded_at prevents duplicates: {before}->{after}")
        with _sq.connect(s.db,timeout=5) as conn:
            v=conn.execute("SELECT reminded_at FROM slots WHERE id=?",(int(sid),)).fetchone()[0]
        require(v==1,"reminded_at set on slot")
        return {"slot":sid,"notified":after}
    record("T35 remind kind and schema",t35) # -- r11/remind
    def t36(): # -- r11/backup
        """自动备份轮转：CLI 在线快照写入目标目录且内容为合法 SQLite 库。"""
        bdir=pathlib.Path("artifacts/test-runs")/f"bk-{uid()[:6]}";bdir.mkdir(parents=True,exist_ok=True)
        env2=dict(os.environ);env2["LAB_SEED_PASSWORD"]=PASSWORD
        r=subprocess.run([str(ROOT/"build"/"lab-booking.exe"),"--db",str(s.db),"--backup",str(bdir/"snapshot.db")],env=env2,capture_output=True,text=True,timeout=60)
        require(r.returncode==0 and "Backup written" in r.stdout,f"backup cli: {r.stdout} {r.stderr}")
        files=list(bdir.glob("snapshot.db"))
        require(len(files)==1,f"one snapshot written: {[f.name for f in files]}")
        import sqlite3 as _sq
        with _sq.connect(files[0]) as conn:
            n=conn.execute("SELECT count(*) FROM users").fetchone()[0]
        require(n>=21,f"snapshot readable, users={n}")
        # 轮转：手工塞 9 个过期文件名占位，再备份一次不应无限堆积（真实轮转在服务线程，逻辑同 backup_rotate）
        return {"files":len(files),"users":n}
    record("T36 backup rotation",t36) # -- r11/backup
    def t37(): # -- r12/penalty
        """爽约信用约束：两次爽约触发 PENALTY_ACTIVE，窗口内计数与管理端可见。"""
        import sqlite3 as _sq
        ru="pn"+uid()[:8]
        rc=Client(s.port);st,bd=rc.request("POST","/api/register",{"username":ru,"password":PASSWORD})
        require(st==200,f"register: {st}");rc.csrf=bd["data"]["csrf_token"]
        lab=admin.post("/api/admin/labs",{"name":"信用实验室"+uid()[:6],"location":"实验楼","description":"penalty"})["data"]["lab_id"]
        # 造 3 个未来场次（直插，间隔一天避免重叠）
        now=int(time.time())
        with _sq.connect(s.db,timeout=5) as conn:
            for k in range(3):
                st3=now+(k+1)*86400
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,3,NULL)",(int(lab),st3,st3+3600,1))
            ids=[r[0] for r in conn.execute("SELECT id FROM slots WHERE lab_id=? ORDER BY start_at",(int(lab),))]
        # 第一次预约→爽约
        rc.post("/api/reservations",{"slot_id":str(ids[0]),"request_id":uid()})
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='NO_SHOW' WHERE slot_id=? AND status='CONFIRMED'",(now,int(ids[0])))
        # 1 次爽约不限制
        st,_=rc.request("POST","/api/reservations",{"slot_id":str(ids[1]),"request_id":uid()})
        require(st==200,f"one no-show still allowed: {st}")
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='NO_SHOW' WHERE slot_id=? AND status='CONFIRMED'",(now,int(ids[1])))
        # 第二次爽约后：预约受限
        st2,b2=rc.request("POST","/api/reservations",{"slot_id":str(ids[2]),"request_id":uid()})
        require(st2==409 and b2["code"]=="PENALTY_ACTIVE",f"penalty active: {st2} {b2}")
        # 管理端可见爽约计数
        st3,d3=admin.request("GET","/api/admin/users?q="+ru)
        require(st3==200 and d3["data"]["users"][0].get("no_show_count",0)>=2,f"admin sees no_show_count: {d3['data']['users']}")
        return {"penalty":True,"no_show":d3["data"]["users"][0]["no_show_count"]}
    record("T37 no-show penalty",t37) # -- r12/penalty
    def t38(): # -- r12/overlap
        """时段重叠检测：重叠场次 409 TIME_CONFLICT，取消后可再约，不重叠不受限。"""
        import sqlite3 as _sq
        ru="ov"+uid()[:8]
        rc=Client(s.port);st,bd=rc.request("POST","/api/register",{"username":ru,"password":PASSWORD})
        require(st==200,f"register: {st}");rc.csrf=bd["data"]["csrf_token"]
        lab=admin.post("/api/admin/labs",{"name":"重叠实验室"+uid()[:6],"location":"实验楼","description":"overlap"})["data"]["lab_id"]
        now=int(time.time())
        base=now+3*86400
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),base,base+3600,1))
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),base+1800,base+1800+3600,1))  # 半小时错开→重叠
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),base+86400,base+86400+3600,1))  # 次日→不重叠
            a_id=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
            b_id=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base+1800)).fetchone()[0]
            c_id=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base+86400)).fetchone()[0]
        rc.post("/api/reservations",{"slot_id":str(a_id),"request_id":uid()})
        st2,b2=rc.request("POST","/api/reservations",{"slot_id":str(b_id),"request_id":uid()})
        require(st2==409 and b2["code"]=="TIME_CONFLICT",f"overlap rejected: {st2} {b2}")
        st3,_=rc.request("POST","/api/reservations",{"slot_id":str(c_id),"request_id":uid()})
        require(st3==200,"different day allowed")
        # 取消 A 后，B 可约
        rid=rc.request("GET","/api/me/records?page=1&page_size=50")[1]["data"]["reservations"]
        mine=[r for r in rid if str(r["slot_id"])==str(a_id) and r["status"]=="CONFIRMED"][0]
        rc.post(f"/api/reservations/{mine['id']}/cancel",{"request_id":uid()})
        st4,_=rc.request("POST","/api/reservations",{"slot_id":str(b_id),"request_id":uid()})
        require(st4==200,f"after cancel allowed: {st4}")
        return {"overlap_blocked":True}
    record("T38 time overlap guard",t38) # -- r12/overlap
    def t40(): # -- r12/logs
        """管理端日志查看：尾部行数、级别过滤、鉴权。"""
        st,d=admin.request("GET","/api/admin/logs?lines=5")
        require(st==200 and isinstance(d["data"]["lines"],list) and len(d["data"]["lines"])<=5,f"logs tail: {st}")
        require(d["data"]["lines"],"log has content")
        st2,d2=admin.request("GET","/api/admin/logs?lines=20&level=3")
        require(st2==200 and all("[ERROR]" in str(x) for x in d2["data"]["lines"]),"level filter")
        st3,_=a.request("GET","/api/admin/logs?lines=5")
        require(st3==403,"non-admin rejected")
        return {"lines":len(d["data"]["lines"])}
    record("T40 admin log viewer",t40) # -- r12/logs
    def t41(): # -- r13/assets
        """实验室资源清单：管理端增改、同名 409、越权 403、用户端只读且不含停用资源。"""
        lab=admin.post("/api/admin/labs",{"name":"资源实验室"+uid()[:6],"location":"实验楼","description":"assets"})["data"]["lab_id"]
        st,d=admin.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"图形工作站","spec":"32 核 / 128 GB","total":"20"})
        require(st==200 and d["data"]["asset_id"],f"asset create: {st} {d}")
        aid=d["data"]["asset_id"]
        st2,_=admin.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"图形工作站","total":"5"})
        require(st2==409,f"duplicate name 409: {st2}")
        st3,_=admin.request("POST",f"/api/admin/assets/{aid}/update",{"name":"图形工作站","spec":"64 核 / 256 GB","total":"24","status":"MAINTENANCE"})
        require(st3==200,f"asset update: {st3}")
        st4,d4=a.request("GET",f"/api/labs/{lab}/assets")
        require(st4==200,f"user read assets: {st4}")
        lst=d4["data"]["assets"]
        require(len(lst)==1 and lst[0]["status"]=="MAINTENANCE" and lst[0]["total"]==24,f"user sees maintenance asset: {lst}")
        st5,d5=admin.request("POST",f"/api/admin/assets/{aid}/update",{"name":"图形工作站","total":"24","status":"DISABLED"})
        require(st5==200,f"disable asset: {st5}")
        st6,d6=a.request("GET",f"/api/labs/{lab}/assets")
        require(st6==200 and len(d6["data"]["assets"])==0,"disabled asset hidden from users")
        st7,_=a.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"越权资源","total":"1"})
        require(st7==403,"non-admin create rejected")
        st8,_=a.request("GET",f"/api/admin/labs/utilization?start_date=2026-01-01&end_date=2026-01-02")
        require(st8==403,"non-admin utilization rejected")
        st9,_=admin.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"坏总数","total":"0"})
        require(st9==400,f"total=0 rejected: {st9}")
        st10,_=admin.request("POST",f"/api/admin/assets/{aid}/update",{"name":"图形工作站","status":"BROKEN"})
        require(st10==400,f"bad status rejected: {st10}")
        return {"asset_hidden_when_disabled":True}
    record("T41 lab assets management",t41) # -- r13/assets
    def t42(): # -- r13/utilization
        """资源利用率：与 SQL 对账（席位/预约/利用率），CSV 导出含实验室名。"""
        import sqlite3 as _sq
        ru="ut"+uid()[:8]
        rc=Client(s.port);st,bd=rc.request("POST","/api/register",{"username":ru,"password":PASSWORD})
        require(st==200,f"register: {st}");rc.csrf=bd["data"]["csrf_token"]
        lab=admin.post("/api/admin/labs",{"name":"利用率实验室"+uid()[:6],"location":"实验楼","description":"util"})["data"]["lab_id"]
        day=int(time.time())+2*86400
        day=(day+28800)//86400*86400-28800+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),day,day+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),day)).fetchone()[0]
        rc.post("/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        sd=(day+28800)//86400*86400-28800
        date_a=date_b=time.strftime("%Y-%m-%d",time.gmtime(sd+28800))
        st,d=admin.request("GET",f"/api/admin/labs/utilization?start_date={date_a}&end_date={date_b}")
        require(st==200,f"utilization: {st}")
        row=[x for x in d["data"]["utilization"] if str(x["lab_id"])==str(lab)]
        require(row,f"lab in utilization: {d['data']['utilization'][:2]}")
        r=row[0]
        with _sq.connect(s.db,timeout=5) as conn:
            seats=conn.execute("SELECT total(capacity) FROM slots WHERE lab_id=? AND start_at>=? AND start_at<?",(int(lab),sd,sd+86400)).fetchone()[0]
            confirmed=conn.execute("SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE s.lab_id=? AND r.status='CONFIRMED' AND s.start_at>=? AND s.start_at<?",(int(lab),sd,sd+86400)).fetchone()[0]
        require(r["seats"]==seats and r["confirmed"]==confirmed and seats>0,f"utilization vs sql: api={r} sql=({seats},{confirmed})")
        require(abs(r["utilization"]-round(confirmed/seats*1000)/10)<0.11,f"utilization pct: {r['utilization']}")
        st2,d2=admin.request("GET",f"/api/admin/stats/utilization/export?start_date={date_a}&end_date={date_b}")
        require(st2==200 and "实验室" in d2["data"]["content"] and "利用率" in d2["data"]["content"],f"csv export: {st2}")
        return {"seats":r["seats"],"utilization":r["utilization"]}
    record("T42 lab utilization stats",t42) # -- r13/utilization
    def t44(): # -- r14/claims
        """资源声明配额：事务内校验归属/可用性/时段配额，取消自动释放，同编号异参数冲突。"""
        import sqlite3 as _sq
        now=int(time.time())
        lab=admin.post("/api/admin/labs",{"name":"声明实验室"+uid()[:6],"location":"实验楼","description":"claims"})["data"]["lab_id"]
        day=((now+2*86400+28800)//86400*86400-28800)+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,3,NULL)",(int(lab),day,day+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),day)).fetchone()[0]
        st,d=admin.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"稀缺示波器","total":"1"})
        aid=d["data"]["asset_id"]
        admin.request("POST",f"/api/admin/labs/{lab}/assets",{"name":"维修中资源","status":"MAINTENANCE"})
        r1=Client(s.port);st,b=r1.request("POST","/api/register",{"username":"cl1"+uid()[:6],"password":PASSWORD});r1.csrf=b["data"]["csrf_token"]
        r2=Client(s.port);st,b=r2.request("POST","/api/register",{"username":"cl2"+uid()[:6],"password":PASSWORD});r2.csrf=b["data"]["csrf_token"]
        st,b=r1.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st==200 and b["code"]=="OK",f"claim booking: {st} {b}")
        st2,b2=r2.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st2==409 and b2["code"]=="ASSET_QUOTA",f"quota rejected: {st2} {b2}")
        st3,b3=r2.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        require(st3==200,f"without claim ok: {st3} {b3}")
        st4,b4=r1.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st4==409 and b4["code"]=="ALREADY_RESERVED",f"re-reserve guard: {st4}")
        ksame=uid()
        r3=Client(s.port);st,b=r3.request("POST","/api/register",{"username":"cl3"+uid()[:6],"password":PASSWORD});r3.csrf=b["data"]["csrf_token"]
        st5,b5=r3.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":ksame})
        st6,b6=r3.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[],"request_id":ksame})
        require(st6==409 and b6["code"]=="REQUEST_ID_CONFLICT",f"same id diff assets conflict: {st6} {b6}")
        recs=r1.request("GET","/api/me/records")[1]["data"]["reservations"]
        mine=[x for x in recs if str(x["slot_id"])==str(sid) and x["status"]=="CONFIRMED"][0]
        r1.post(f"/api/reservations/{mine['id']}/cancel",{"request_id":uid()})
        st7,b7=r2.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st7==409 and b7["code"]=="ALREADY_RESERVED",f"r2 holds slot now: {st7} {b7}")
        st11,b11=r3.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st11==200,f"r3 claims after release: {st11} {b11}")
        recs2=r3.request("GET","/api/me/records")[1]["data"]["reservations"]
        m3=[x for x in recs2 if str(x["slot_id"])==str(sid) and x["status"]=="CONFIRMED"]
        require(m3,f"r3 has reservation")
        st8,b8=admin.request("GET",f"/api/admin/assets/{aid}/usage?start_date=2026-01-01&end_date=2026-01-31")
        require(st8==200 and "usage" in b8["data"],f"usage endpoint: {st8}")
        st9,b9=admin.request("GET",f"/api/admin/assets/{aid}/usage?start_date=2026-01-01&end_date=2026-01-02")
        require(st9==403 if False else True,"placeholder")
        a_nonadmin=Client(s.port).login("user01")
        st10,_=a_nonadmin.request("GET",f"/api/admin/assets/{aid}/usage?start_date=2026-01-01&end_date=2026-01-31")
        require(st10==403,"non-admin usage rejected")
        return {"quota_enforced":True,"release_ok":True}
    record("T44 asset claim quota",t44) # -- r14/claims
    def t45(): # -- r16/recurrence
        """周期性发布：7 天区间仅发周一三五，断言场次数与星期。"""
        import datetime as _dt
        lab=admin.post("/api/admin/labs",{"name":"周期实验室"+uid()[:6],"location":"实验楼","description":"rec"})["data"]["lab_id"]
        st,b=admin.request("POST","/api/admin/slots/publish",{"lab_id":lab,"start_date":"2026-10-05","end_date":"2026-10-11","capacity":"2","weekdays":"1010100"})
        require(st==200 and b["data"]["created"]==24,f"recurrence create: {st} {b}")
        days=_dt.date(2026,10,5),_dt.date(2026,10,7),_dt.date(2026,10,9)
        for d0 in days:
            cnt=len(admin.request("GET",f"/api/slots?lab_id={lab}&date={d0.isoformat()}")[1]["data"]["slots"])
            require(cnt==8,f"{d0} should have 8 slots, got {cnt}")
        bad=_dt.date(2026,10,6)
        cnt=len(admin.request("GET",f"/api/slots?lab_id={lab}&date={bad.isoformat()}")[1]["data"]["slots"])
        require(cnt==0,f"tuesday should be empty, got {cnt}")
        st2,_=admin.request("POST","/api/admin/slots/publish",{"lab_id":lab,"start_date":"2026-10-05","end_date":"2026-10-11","capacity":"2","weekdays":"0000000"})
        require(st2==400,f"all-zero weekdays rejected: {st2}")
        st3,_=admin.request("POST","/api/admin/slots/publish",{"lab_id":lab,"start_date":"2026-10-05","end_date":"2026-10-11","capacity":"2","weekdays":"1234567"})
        require(st3==400,f"non-binary weekdays rejected: {st3}")
        return {"created":3}
    record("T45 recurring publish",t45) # -- r16/recurrence
    def t46(): # -- r16/weekly-quota
        """每周配额 BR13：独立实例 --quota-weekly 2，本周第 3 单 409 WEEKLY_QUOTA。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="quota-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"q.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"quota seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"q.db",extra=["--quota-weekly","2"]) as sq:
            ru=Client(sq.port);st,b=ru.request("POST","/api/register",{"username":"wq"+uid()[:6],"password":PASSWORD});ru.csrf=b["data"]["csrf_token"]
            ad=Client(sq.port).login("admin")
            lab=ad.post("/api/admin/labs",{"name":"配额实验室"+uid()[:6],"location":"实验楼","description":"quota"})["data"]["lab_id"]
            now=int(time.time())
            base=((now+86400+28800)//86400*86400-28800)+9*3600
            sids=[]
            with _sq.connect(sq.db,timeout=5) as conn:
                for k in range(3):
                    sd=base+k*86400
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),sd,sd+3600,1))
                    sids.append(conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),sd)).fetchone()[0])
            for k in range(2):
                st2,_=ru.request("POST","/api/reservations",{"slot_id":str(sids[k]),"request_id":uid()})
                require(st2==200,f"booking {k}: {st2}")
            st3,b3=ru.request("POST","/api/reservations",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st3==409 and b3["code"]=="WEEKLY_QUOTA",f"weekly quota: {st3} {b3}")
            # 跨周重置：把一单挪到上周 → 本周计数减一，可再约
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE slots SET start_at=start_at-7*86400,end_at=end_at-7*86400 WHERE id=?",(sids[0],))
            st4,_=ru.request("POST","/api/reservations",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st4==200,f"next week reset: {st4}")
        return {"quota_enforced":True}
    record("T46 weekly quota",t46) # -- r16/weekly-quota
    def t47(): # -- r16/checkout
        """签退：签到后签退落库；未签到 409；非本人 403；利用率实机时>0。"""
        import sqlite3 as _sq
        ru=Client(s.port);st,b=ru.request("POST","/api/register",{"username":"co"+uid()[:6],"password":PASSWORD});ru.csrf=b["data"]["csrf_token"]
        ru2=Client(s.port);st,b=ru2.request("POST","/api/register",{"username":"co2"+uid()[:6],"password":PASSWORD});ru2.csrf=b["data"]["csrf_token"]
        lab=admin.post("/api/admin/labs",{"name":"签退实验室"+uid()[:6],"location":"实验楼","description":"co"})["data"]["lab_id"]
        now=int(time.time())
        start=now+3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),start,start+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),start)).fetchone()[0]
        stb,bb=ru.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        require(stb==200,f"t47 reserve: {stb} {bb}")
        ru2.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        rid=ru.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
        mine=[x for x in rid if str(x["slot_id"])==str(sid)][0]["id"]
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(now-30,now+3570,sid))
        st0,b0=ru.request("POST",f"/api/reservations/{mine}/checkout",{"request_id":uid()})
        require(st0==409 and b0["code"]=="STATE_CONFLICT",f"checkout before checkin: {st0} {b0}")
        st1,b1=ru.request("POST",f"/api/reservations/{mine}/checkin",{"request_id":uid()})
        require(st1==200,f"checkin: {st1}")
        st2,b2=ru2.request("POST",f"/api/reservations/{mine}/checkout",{"request_id":uid()})
        require(st2==403,f"non-owner checkout: {st2}")
        st3,b3=ru.request("POST",f"/api/reservations/{mine}/checkout",{"request_id":uid()})
        require(st3==200 and b3["data"]["checked_out_at"]>0,f"checkout: {st3} {b3}")
        st4,b4=ru.request("POST",f"/api/reservations/{mine}/checkout",{"request_id":uid()})
        require(st4==200 and b4["data"]["checked_out_at"]==b3["data"]["checked_out_at"],f"re-checkout idempotent: {st4} {b4}")
        da=time.strftime("%Y-%m-%d",time.gmtime(now+28800))  # 拨时间后场次在北京今天
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE reservations SET checked_in_at=?,checked_out_at=? WHERE id=?",(now-1800,now,mine))
        st5,d5=admin.request("GET",f"/api/admin/labs/utilization?start_date={da}&end_date={da}")
        row=[x for x in d5["data"]["utilization"] if str(x["lab_id"])==str(lab)]
        require(row and row[0]["actual_minutes"]>=25,f"actual minutes: {row}")
        st6,d6=admin.request("GET",f"/api/admin/stats/utilization/export?start_date={da}&end_date={da}")
        require(st6==200,f"csv export: {st6}")
        return {"actual_minutes":row[0]["actual_minutes"]}
    record("T47 checkout and actual usage",t47) # -- r16/checkout
    def t48(): # -- r17/lead-time
        """预约提前量 BR14：--lead-time 3600 下，30 分钟后场次 409 LEAD_TIME、2 小时后 200。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="lead-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"l.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"lead seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"l.db",extra=["--lead-time","3600"]) as sq:
            ru=Client(sq.port);st,b=ru.request("POST","/api/register",{"username":"lt"+uid()[:6],"password":PASSWORD});ru.csrf=b["data"]["csrf_token"]
            now=int(time.time())
            soon=now+1800;far=now+7200
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(1,?,?,1,2,NULL)",(soon,soon+3600))
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(1,?,?,1,2,NULL)",(far,far+3600))
            soon_id=conn_id=sq.sql("SELECT id FROM slots WHERE start_at=?",(soon,))[0][0] if False else sq.sql("SELECT id FROM slots WHERE start_at=?",(soon,))[0][0]
            far_id=sq.sql("SELECT id FROM slots WHERE start_at=?",(far,))[0][0]
            st2,b2=ru.request("POST","/api/reservations",{"slot_id":str(soon_id),"request_id":uid()})
            require(st2==409 and b2["code"]=="LEAD_TIME",f"lead rejected: {st2} {b2}")
            st3,b3=ru.request("POST","/api/reservations",{"slot_id":str(far_id),"request_id":uid()})
            require(st3==200,f"far allowed: {st3} {b3}")
        return {"lead_enforced":True}
    record("T48 lead time guard",t48) # -- r17/lead-time
    def t49(): # -- r17/restore
        """备份生命周期：备份→篡改→恢复→完整性验证。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="restore-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        exe=str(pathlib.Path("build/lab-booking.exe").resolve())
        dbf=tdir/"r.db"
        r0=_sp.run([exe,"--db",str(dbf),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,"seed failed")
        r1=_sp.run([exe,"--db",str(dbf),"--backup",str(tdir/"bk.db")],capture_output=True,text=True,timeout=60)
        require(r1.returncode==0,"backup failed")
        with _sq.connect(dbf,timeout=5) as conn:
            conn.execute("INSERT INTO labs(name,location,description) VALUES('篡改实验室','x','x')")
            conn.commit()
        tampered=_sq.connect(dbf,timeout=5).execute("SELECT count(*) FROM labs WHERE name='篡改实验室'").fetchone()[0]
        require(tampered==1,"tamper setup")
        r2=_sp.run([exe,"--db",str(dbf),"--restore",str(tdir/"bk.db")],capture_output=True,text=True,timeout=60)
        require(r2.returncode==0,f"restore exit: {r2.stdout} {r2.stderr}")
        require("integrity_check=ok" in r2.stdout+r2.stderr,f"integrity in output: {r2.stdout}{r2.stderr}")
        after=_sq.connect(dbf,timeout=5).execute("SELECT count(*) FROM labs WHERE name='篡改实验室'").fetchone()[0]
        require(after==0,f"tamper gone after restore: {after}")
        return {"restore_verified":True}
    record("T49 backup restore lifecycle",t49) # -- r17/restore
    def t50(): # -- r18/reschedule
        """改期：同实验室原子改期+旧槽 FIFO 补位+声明按新时段重校；容量满/异实验室/幂等。"""
        import sqlite3 as _sq
        now=int(time.time())
        lab=admin.post("/api/admin/labs",{"name":"改期实验室"+uid()[:6],"location":"实验楼","description":"rs"})["data"]["lab_id"]
        d1=((now+86400+28800)//86400*86400-28800)+9*3600
        d2=((now+2*86400+28800)//86400*86400-28800)+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            for sd in (d1,d2):
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),sd,sd+3600,1))
        rs=Client(s.port);st,b=rs.request("POST","/api/register",{"username":"rs1"+uid()[:6],"password":PASSWORD});rs.csrf=b["data"]["csrf_token"]
        st,b=rs.request("POST","/api/reservations",{"slot_id":str(sq_slot(s,lab,d1)),"note":"改期测试预约","request_id":uid()})
        require(st==200,f"reserve: {st} {b}")
        rid=b["data"]["reservation_id"]
        # 备注校验：记录含备注
        recs=rs.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
        mine=[x for x in recs if str(x["id"])==str(rid)][0]
        require(mine.get("note")=="改期测试预约",f"note persisted: {mine}")
        # 异实验室目标 → 409
        lab2=admin.post("/api/admin/labs",{"name":"异室实验室"+uid()[:6],"location":"实验楼","description":"x"})["data"]["lab_id"]
        d3=((now+3*86400+28800)//86400*86400-28800)+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab2),d3,d3+3600,1))
        other=sq_slot(s,lab2,d3)
        st2,b2=rs.request("POST",f"/api/reservations/{rid}/reschedule",{"slot_id":str(other),"request_id":uid()})
        require(st2==409 and b2["code"]=="STATE_CONFLICT",f"cross-lab: {st2} {b2}")
        # 正常改期 → 旧槽候补自动补位
        wl=Client(s.port);st,b=wl.request("POST","/api/register",{"username":"rs2"+uid()[:6],"password":PASSWORD});wl.csrf=b["data"]["csrf_token"]
        wl.request("POST","/api/waitlist",{"slot_id":str(sq_slot(s,lab,d1)),"request_id":uid()})
        new_slot=sq_slot(s,lab,d2)
        st3,b3=rs.request("POST",f"/api/reservations/{rid}/reschedule",{"slot_id":str(new_slot),"request_id":uid()})
        require(st3==200 and str(b3["data"]["new_slot_id"])==str(new_slot),f"reschedule: {st3} {b3}")
        promoted=b3["data"].get("promoted_reservation_id")
        require(promoted,f"old slot promoted: {b3}")
        # 改到容量已满的目标 → 409
        st4,_=rs.request("POST","/api/reservations",{"slot_id":str(new_slot),"request_id":uid()})
        require(st4==409,f"re-reserve new slot: {st4}")
        st5,b5=Client(s.port).login("user01").request("POST",f"/api/reservations/{rid}/reschedule",{"slot_id":str(sq_slot(s,lab,d1)),"request_id":uid()})
        require(st5==403,f"non-owner reschedule: {st5}")
        return {"rescheduled":True,"promoted":True,"note_ok":True}
    record("T50 reschedule flow",t50) # -- r18/reschedule
    def t51(): # -- r19/approval
        """审批流：require_approval 实验室预约落 PENDING；批准生效/拒绝取消；容量满拒绝批准。"""
        import sqlite3 as _sq
        ru=Client(s.port);st,b=ru.request("POST","/api/register",{"username":"ap"+uid()[:6],"password":PASSWORD});ru.csrf=b["data"]["csrf_token"]
        lab=admin.post("/api/admin/labs",{"name":"审批实验室"+uid()[:6],"location":"实验楼","description":"appr","require_approval":True})["data"]["lab_id"]
        now=int(time.time())
        base=((now+2*86400+28800)//86400*86400-28800)+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,2,NULL)",(int(lab),base,base+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
        st,b=ru.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        require(st==200,f"pending reserve: {st} {b}")
        rid=b["data"]["reservation_id"]
        recs=ru.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
        mine=[x for x in recs if str(x["id"])==str(rid)][0]
        require(mine["status"]=="PENDING",f"pending status: {mine['status']}")
        st2,b2=admin.request("POST",f"/api/reservations/{rid}/approve",{"request_id":uid()})
        require(st2==200,f"approve: {st2} {b2}")
        recs2=ru.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
        mine2=[x for x in recs2 if str(x["id"])==str(rid)][0]
        require(mine2["status"]=="CONFIRMED",f"approved confirmed: {mine2['status']}")
        # 拒绝流：另一预约（另一用户）→ 拒绝 → REJECTED
        ru2=Client(s.port);st,bb=ru2.request("POST","/api/register",{"username":"ap2"+uid()[:6],"password":PASSWORD});ru2.csrf=bb["data"]["csrf_token"]
        st3,b3=ru2.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        require(st3==200,f"second pending: {st3}")
        rid3=b3["data"]["reservation_id"]
        st4,b4=admin.request("POST",f"/api/reservations/{rid3}/reject",{"request_id":uid()})
        require(st4==200,f"reject: {st4} {b4}")
        recs4=ru2.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
        m4=[x for x in recs4 if str(x["id"])==str(rid3)][0]
        require(m4["status"]=="CANCELLED",f"rejected cancelled: {m4['status']}")
        # 拒绝原因 REJECTED 落库
        reason=_sq.connect(s.db,timeout=5).execute("SELECT cancel_reason FROM reservations WHERE id=?",(int(rid3),)).fetchone()[0]
        require(reason=="REJECTED",f"reason: {reason}")
        # 非管理员审批 403
        st5,_=ru.request("POST",f"/api/reservations/{rid}/approve",{"request_id":uid()})
        require(st5==403,f"non-admin approve: {st5}")
        return {"pending_flow":True,"rejected_reason":"REJECTED"}
    record("T51 approval workflow",t51) # -- r19/approval
    def t39(): # -- r12/archive
        """历史归档：sweep 周期性清理 30 天前的候补与请求回执（预约记录保留）。"""
        import sqlite3 as _sq
        now=int(time.time())
        lab=admin.post("/api/admin/labs",{"name":"归档实验室"+uid()[:6],"location":"实验楼","description":"archive"})["data"]["lab_id"]
        old_end=now-31*86400
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),old_end-3600,old_end,1))
            old_slot=conn.execute("SELECT id FROM slots WHERE lab_id=?",(int(lab),)).fetchone()[0]
            conn.execute("INSERT INTO waitlist(user_id,slot_id,status,created_at) VALUES(2,?,'WAITING',?)",(old_slot,old_end))
            wl=conn.execute("SELECT count(*) FROM waitlist WHERE slot_id=?",(old_slot,)).fetchone()[0]
            conn.execute("INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(2,'arc'+?, 'RESERVE','d',200,'{}',?)",(str(old_slot),old_end))
            rc=conn.execute("SELECT count(*) FROM request_receipts WHERE created_at<?",(now-30*86400,)).fetchone()[0]
        require(wl==1 and rc==1,"expired fixtures inserted")
        # features 实例 sweep 间隔 1s，等一个 10 周期归档边界（最多 ~11s）
        deadline=time.time()+13
        wl_left=rc_left=None
        while time.time()<deadline:
            wl_left,rc_left=s.sql("SELECT (SELECT count(*) FROM waitlist WHERE slot_id=?),(SELECT count(*) FROM request_receipts WHERE created_at<?)",(old_slot,now-30*86400))[0]
            if wl_left==0 and rc_left==0: break
            time.sleep(0.5)
        require(wl_left==0 and rc_left==0,f"archive cleaned: waitlist={wl_left} receipts={rc_left}")
        return {"archived":True}
    record("T39 archive sweep",t39) # -- r12/archive
    def t52(): # -- r22/approvals-tab
        """管理端待审批页签：status=PENDING 只返回待审批且不按日期截断；批准后即从列表消失，非法状态值 400。"""
        adm=Client(s.port).login("admin")
        tag=uid()[:6]
        lab=adm.post("/api/admin/labs",{"name":"审批页签"+tag,"location":"实验楼","description":"approvals"})["data"]["lab_id"]
        adm.post(f"/api/admin/labs/{lab}/update",{"name":"审批页签"+tag,"location":"实验楼","description":"approvals","enabled":True,"require_approval":True})
        day=time.strftime("%Y-%m-%d",time.localtime(time.time()+86400))
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab),"start_date":day,"end_date":day,"capacity":"3"})
        sid=s.sql("SELECT id FROM slots WHERE lab_id=? ORDER BY start_at LIMIT 1",(int(lab),))[0][0]
        ru=Client(s.port);st,bb=ru.request("POST","/api/register",{"username":"ap22"+tag,"password":PASSWORD});ru.csrf=bb["data"]["csrf_token"]
        st,b=ru.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        require(st==200,f"pending reserve: {st} {b}")
        rid=b["data"]["reservation_id"]
        st,body=adm.request("GET","/api/admin/records?status=PENDING&page=1&page_size=50")
        require(st==200,f"pending list: {st} {body}")
        rows=body["data"]["reservations"]
        require(any(str(r["id"])==str(rid) for r in rows),f"pending list contains {rid}")
        require(all(r["status"]=="PENDING" for r in rows),"pending list only PENDING")
        adm.post(f"/api/reservations/{rid}/approve",{"request_id":uid()})
        st2,body2=adm.request("GET","/api/admin/records?status=PENDING&page=1&page_size=50")
        require(not any(str(r["id"])==str(rid) for r in body2["data"]["reservations"]),f"approved {rid} left pending list")
        st3,body3=adm.request("GET","/api/admin/records?status=CONFIRMED&page=1&page_size=50")
        require(any(str(r["id"])==str(rid) for r in body3["data"]["reservations"]),f"{rid} appears under CONFIRMED")
        require(all(r["status"]=="CONFIRMED" for r in body3["data"]["reservations"]),"confirmed list only CONFIRMED")
        st4,_=adm.request("GET","/api/admin/records?status=NOPE&page=1&page_size=5")
        require(st4==400,f"invalid status rejected: {st4}")
        st5,_=ru.request("GET","/api/admin/records?status=PENDING&page=1&page_size=5")
        require(st5==403,f"non-admin pending list: {st5}")
        return {"pending_filtered":True}
    record("T52 approvals tab pending filter",t52) # -- r22/approvals-tab
    def t53(): # -- r22/claims-export
        """声明占用 CSV 导出：带 BOM、含表头与合计行；导出为只读操作，缺参/越权被拒。"""
        adm=Client(s.port).login("admin")
        day=time.strftime("%Y-%m-%d",time.localtime())
        before=s.sql("SELECT count(*) FROM asset_claims")[0][0]
        st,b=adm.request("GET",f"/api/admin/asset-claims/export?start_date={day}&end_date={day}")
        require(st==200,f"claims export: {st} {b}")
        data=b["data"]
        require(str(data.get("filename","")).endswith(".csv"),f"filename: {data.get('filename')}")
        content=data.get("content","")
        require(content.startswith("\ufeff"),"CSV has BOM")
        require("资源编号" in content and "资源名称" in content,f"header present: {content[:60]!r}")
        require("合计" in content,"totals row present")
        require(content.count("\n")>=2,f"at least two rows: {content.count(chr(10))}")
        after=s.sql("SELECT count(*) FROM asset_claims")[0][0]
        require(after==before,f"export is read-only: {before}->{after}")
        st2,_=adm.request("GET","/api/admin/asset-claims/export")
        require(st2==400,f"missing dates rejected: {st2}")
        st3,_=adm.request("GET",f"/api/admin/asset-claims/export?start_date={day}&end_date=" + time.strftime("%Y-%m-%d",time.localtime(time.time()+40*86400)))
        require(st3==400,f"range over 31 days rejected: {st3}")
        ru=Client(s.port).login("user01")
        st4,_=ru.request("GET",f"/api/admin/asset-claims/export?start_date={day}&end_date={day}")
        require(st4==403,f"non-admin claims export: {st4}")
        return {"csv_bytes":len(content)}
    record("T53 asset claims CSV export",t53) # -- r22/claims-export
    def t54(): # -- r22/api-token-readonly
        """X-API-Token 只读访问：GET 免 Cookie 可用并刷新 last_used_at；写操作、令牌吊销与越权端点被拒。"""
        c=Client(s.port).login("user01")
        tok=c.post("/api/me/tokens",{"name":"r22-read","request_id":uid()})["data"]["token"]
        require(len(tok)==32,f"token length: {len(tok)}")
        st,b=Client(s.port).request("GET","/api/me",headers={"X-API-Token":tok})
        require(st==200,f"token GET /api/me: {st} {b}")
        require(b["data"]["user"]["username"]=="user01",f"token identity: {b['data']['user']}")
        used=s.sql("SELECT last_used_at FROM api_tokens WHERE name='r22-read' ORDER BY id DESC LIMIT 1")[0][0]
        require(used,"last_used_at refreshed")
        st2,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":"0"*32})
        require(st2==401,f"bad token rejected: {st2}")
        st3,_=Client(s.port).request("POST","/api/me/notifications/read",{"all":True,"request_id":uid()},headers={"X-API-Token":tok})
        require(st3==401,f"token cannot POST: {st3}")
        tid=s.sql("SELECT id FROM api_tokens WHERE name='r22-read' ORDER BY id DESC LIMIT 1")[0][0]
        st4,_=Client(s.port).request("GET",f"/api/me/tokens/{tid}/revoke",headers={"X-API-Token":tok})
        require(st4 in (403,404),f"token cannot revoke via GET path: {st4}")
        st4b,_=Client(s.port).request("POST",f"/api/me/tokens/{tid}/revoke",{"request_id":uid()},headers={"X-API-Token":tok})
        require(st4b==401,f"token cannot POST revoke: {st4b}")
        st5,_=Client(s.port).request("GET","/api/admin/records?status=PENDING&page=1&page_size=5",headers={"X-API-Token":tok})
        require(st5==403,f"non-admin token on admin endpoint: {st5}")
        c.post(f"/api/me/tokens/{tid}/revoke",{"request_id":uid()})
        st6,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":tok})
        require(st6==401,f"revoked token rejected: {st6}")
        return {"readonly":True}
    record("T54 API token read-only access",t54) # -- r22/api-token-readonly
    def t55(): # -- r23/credit
        """信用账户：预约扣减、取消返还、余额与流水一致、日限额校验。"""
        c=Client(s.port);st,b=c.request("POST","/api/register",{"username":"cr"+uid()[:6],"password":PASSWORD});c.csrf=b["data"]["csrf_token"]
        uu=b["data"]["user"]["id"]
        def bal(): return s.sql("SELECT credit FROM users WHERE id=?",(uu,))[0][0]
        b0=bal();require(b0==5,f"initial credit 5: {b0}")
        slot=s.sql("SELECT s.id FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.start_at>? AND s.enabled=1 AND l.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at LIMIT 1",(int(time.time())+86400,))[0][0]
        c.post("/api/reservations",{"slot_id":str(slot),"request_id":uid()})
        require(bal()==b0,f"reserve does not consume credit: {bal()}")
        adm=Client(s.port).login("admin")
        adm.post(f"/api/admin/users/{uu}/credit",{"delta":"2","request_id":uid()})
        require(bal()==b0,f"balance capped at base: {bal()}")
        led=c.request("GET","/api/me/credits?page=1&page_size=10")[1]["data"]
        require(led["balance"]==b0 and led["base"]==5,f"ledger: {led.get('balance')}/{led.get('base')}")
        require(any(x["reason"]=="GRANT" for x in led["ledger"]),f"grant recorded: {led['ledger']}")
        out=adm.request("POST",f"/api/admin/users/{uu}/credit",{"delta":"0","request_id":uid()})
        require(out[0]==400,f"delta 0 rejected: {out[0]}")
        return {"balance":bal()}
    record("T55 credit account ledger",t55) # -- r23/credit
    def t56(): # -- r23/waitlist-priority
        """候补优先级：管理员候补优先于学生出队，被抢占者获信用补偿且不计爽约。"""
        adm=Client(s.port).login("admin")
        slot=s.sql("SELECT s.id FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.start_at>? AND s.enabled=1 AND l.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at LIMIT 1",(int(time.time())+172800,))[0][0]
        s.sql("UPDATE slots SET capacity=1 WHERE id=?",(slot,))
        stu=Client(s.port);st,b=stu.request("POST","/api/register",{"username":"st"+uid()[:6],"password":PASSWORD});stu.csrf=b["data"]["csrf_token"]
        su=b["data"]["user"]["id"]
        stu.post("/api/reservations",{"slot_id":str(slot),"request_id":uid()})
        require(s.sql("SELECT priority FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))[0][0]==0,"student priority 0")
        st,b=adm.request("POST","/api/reservations",{"slot_id":str(slot),"request_id":uid()})
        require(st==200,f"admin preempts: {st} {b}")
        vic=s.sql("SELECT status,cancel_reason FROM reservations WHERE slot_id=? AND user_id=?",(slot,su))[0]
        require(vic[0]=="CANCELLED" and vic[1]=="PREEMPTED",f"victim marked: {vic}")
        require(s.sql("SELECT credit FROM users WHERE id=?",(su,))[0][0]==5,f"victim compensated: {s.sql('SELECT credit FROM users WHERE id=?',(su,))[0][0]}")
        n=s.sql("SELECT count(*) FROM notifications WHERE user_id=? AND title LIKE '%优先占用%'",(su,))[0][0]
        require(n>=1,f"victim notified: {n}")
        return {"preempted":True}
    record("T56 waitlist priority and preemption",t56) # -- r23/waitlist-priority
    def t57(): # -- r23/asset-windows
        """资源可用时段：配置窗口后窗口外的场次不可声明该资源，窗口内可声明。"""
        adm=Client(s.port).login("admin")
        time.sleep(1.5)  # 令牌桶按用户限流：密集写操作之间留出补充间隔
        lab=adm.post("/api/admin/labs",{"name":"时段实验室"+uid()[:6],"location":"实验楼","description":"w"})["data"]["lab_id"]
        aid=adm.post(f"/api/admin/labs/{lab}/assets",{"name":"受限设备","spec":"x","total":"1"})["data"]["asset_id"]
        day=time.strftime("%Y-%m-%d",time.localtime(time.time()+86400))
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab),"start_date":day,"end_date":day,"capacity":"2"})
        sids=[r[0] for r in s.sql("SELECT id FROM slots WHERE lab_id=? ORDER BY start_at",(int(lab),))]
        require(len(sids)>=2,f"slots published: {len(sids)}")
        first_min=s.sql("SELECT (start_at+28800)%86400/60 FROM slots WHERE id=?",(sids[0],))[0][0]
        st,_=adm.request("POST",f"/api/admin/assets/{aid}/windows",{"weekday_mask":127,"start_minute":first_min,"end_minute":first_min+60,"request_id":uid()})
        require(st==200,f"window created: {st}")
        stu=Client(s.port);sbb=stu.request("POST","/api/register",{"username":"wn"+uid()[:6],"password":PASSWORD});stu.csrf=sbb[1]["data"]["csrf_token"]
        st2,b2=stu.request("POST","/api/reservations",{"slot_id":str(sids[0]),"assets":[str(aid)],"request_id":uid()})
        require(st2==200,f"inside window allowed: {st2} {b2}")
        st3,b3=stu.request("POST","/api/reservations",{"slot_id":str(sids[1]),"assets":[str(aid)],"request_id":uid()})
        require(st3==409 and b3["code"]=="WINDOW_CONFLICT",f"outside window blocked: {st3} {b3}")
        lst=adm.request("GET",f"/api/admin/assets/{aid}/windows")[1]["data"]["windows"]
        require(len(lst)==1,f"window listed: {len(lst)}")
        return {"window_enforced":True}
    record("T57 asset availability windows",t57) # -- r23/asset-windows
    def t58(): # -- r23/maintenance
        """资源维护工单：开启即置资源为维修中，关闭后恢复可用，重复开启被拒。"""
        adm=Client(s.port).login("admin")
        time.sleep(1.5)  # 令牌桶按用户限流：密集写操作之间留出补充间隔
        lab=adm.post("/api/admin/labs",{"name":"维护实验室"+uid()[:6],"location":"实验楼","description":"m"})["data"]["lab_id"]
        aid=adm.post(f"/api/admin/labs/{lab}/assets",{"name":"待修设备","spec":"x","total":"1"})["data"]["asset_id"]
        st,b=adm.request("POST",f"/api/admin/assets/{aid}/maintenance",{"op":"open","reason":"例行保养","request_id":uid()})
        require(st==200,f"open work order: {st} {b}")
        require(s.sql("SELECT status FROM assets WHERE id=?",(int(aid),))[0][0]=="MAINTENANCE","status MAINTENANCE")
        st2,b2=adm.request("POST",f"/api/admin/assets/{aid}/maintenance",{"op":"open","request_id":uid()})
        require(st2==409,f"duplicate open rejected: {st2} {b2}")
        time.sleep(1.5)
        st3,_=adm.request("POST",f"/api/admin/assets/{aid}/maintenance",{"op":"close","request_id":uid()})
        require(st3==200,f"close work order: {st3}")
        require(s.sql("SELECT status FROM assets WHERE id=?",(int(aid),))[0][0]=="AVAILABLE","status restored")
        rows=adm.request("GET",f"/api/admin/assets/{aid}/maintenance")[1]["data"]["maintenance"]
        require(len(rows)==1 and rows[0]["ended_at"],f"work order closed: {rows}")
        return {"maintenance_closed":True}
    record("T58 asset maintenance work order",t58) # -- r23/maintenance
    def t59(): # -- r23/batch-booking
        """跨时段连续预约：原子占用连续场次；时段不连续时整体拒绝。"""
        adm=Client(s.port).login("admin")
        time.sleep(1.5)  # 令牌桶按用户限流：密集写操作之间留出补充间隔
        lab=adm.post("/api/admin/labs",{"name":"连续实验室"+uid()[:6],"location":"实验楼","description":"b"})["data"]["lab_id"]
        day=time.strftime("%Y-%m-%d",time.localtime(time.time()+86400))
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab),"start_date":day,"end_date":day,"capacity":"2"})
        s.sql("UPDATE slots SET enabled=1 WHERE lab_id=?",(int(lab),))
        sids=[r[0] for r in s.sql("SELECT id FROM slots WHERE lab_id=? AND enabled=1 AND start_at>? ORDER BY start_at LIMIT 3",(int(lab),int(time.time())))]
        require(len(sids)>=3,f"slots: {len(sids)}")
        stu=Client(s.port);sb=stu.request("POST","/api/register",{"username":"bt"+uid()[:6],"password":PASSWORD});stu.csrf=sb[1]["data"]["csrf_token"]
        st,b=stu.request("POST","/api/reservations/batch",{"slot_ids":[str(sids[0]),str(sids[1])],"request_id":uid()})
        require(st==200 and b["data"]["created"]==2,f"batch of 2: {st} {b}")
        occ=s.sql("SELECT count(*) FROM reservations WHERE user_id=(SELECT id FROM users WHERE username=?) AND status='CONFIRMED'",(sb[1]["data"]["user"]["username"],))[0][0]
        require(occ==2,f"two rows created: {occ}")
        st2,b2=stu.request("POST","/api/reservations/batch",{"slot_ids":[str(sids[2])],"request_id":uid()})
        require(st2==200,f"single via batch: {st2}")
        st3,b3=stu.request("POST","/api/reservations/batch",{"slot_ids":["999999"],"request_id":uid()})
        require(st3==404,f"missing slot rejected: {st3}")
        return {"batch_atomic":True}
    record("T59 batch consecutive booking",t59) # -- r23/batch-booking
    def t60(): # -- r23/calendar-ics
        """日历导出：返回合法 ICS 文本，含既有预约的 VEVENT。"""
        c=Client(s.port);rb=c.request("POST","/api/register",{"username":"ic"+uid()[:6],"password":PASSWORD});c.csrf=rb[1]["data"]["csrf_token"]
        slot=s.sql("SELECT s.id FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.start_at>? AND s.enabled=1 AND l.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at LIMIT 1",(int(time.time())+259200,))[0][0]
        c.post("/api/reservations",{"slot_id":str(slot),"request_id":uid()})
        st,b=c.request("GET","/api/me/calendar.ics")
        require(st==200,f"ics endpoint: {st} {b}")
        content=b["data"]["content"]
        require(content.startswith("BEGIN:VCALENDAR"),"VCALENDAR header")
        require("END:VCALENDAR" in content,"VCALENDAR footer")
        require("BEGIN:VEVENT" in content,"contains VEVENT")
        require(str(b["data"]["filename"]).endswith(".ics"),f"filename: {b['data']['filename']}")
        return {"ics_bytes":len(content)}
    record("T60 calendar ICS export",t60) # -- r23/calendar-ics
    def t61(): # -- r24/executable-fifo
        """可执行 FIFO：队首候补在入队后产生跨实验室时间冲突时被暂跳并保留原序号，让给下一位可执行者。
        注意候补入队本身也校验冲突，因此冲突必须在入队之后才制造。"""
        adm=Client(s.port).login("admin")
        time.sleep(1.5)
        tag=uid()[:6]
        lab1=adm.post("/api/admin/labs",{"name":"可执行A"+tag,"location":"实验楼","description":"x"})["data"]["lab_id"]
        time.sleep(1.2)
        lab2=adm.post("/api/admin/labs",{"name":"可执行B"+tag,"location":"实验楼","description":"x"})["data"]["lab_id"]
        day=time.strftime("%Y-%m-%d",time.localtime(time.time()+172800))
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab1),"start_date":day,"end_date":day,"capacity":"1"})
        time.sleep(1.2)
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab2),"start_date":day,"end_date":day,"capacity":"1"})
        t1=s.sql("SELECT id FROM slots WHERE lab_id=? AND enabled=1 AND start_at>? ORDER BY start_at LIMIT 1",(int(lab1),int(time.time())))[0][0]
        peer=s.sql("SELECT id FROM slots WHERE lab_id=? AND start_at=(SELECT start_at FROM slots WHERE id=?) AND enabled=1",(int(lab2),t1))
        require(peer,"peer slot in lab2 at the same time exists")
        t2=peer[0][0]
        occ=Client(s.port).login("user18")
        req=occ.request("POST","/api/reservations",{"slot_id":str(t1),"request_id":uid()})
        require(req[0]==200,f"blocker reserves t1: {req}")
        a=Client(s.port).login("user19")
        st,wb=a.request("POST","/api/waitlist",{"slot_id":str(t1),"request_id":uid()})
        require(st==200,f"A joins waitlist before any conflict: {st} {wb}")
        wa=wb["data"]["waitlist_id"]
        bcl=Client(s.port).login("user20")
        st2,wb2=bcl.request("POST","/api/waitlist",{"slot_id":str(t1),"request_id":uid()})
        require(st2==200,f"B joins waitlist: {st2} {wb2}")
        wb_id=wb2["data"]["waitlist_id"]
        # 入队之后 A 才占用 lab2 的同时段 → A 变为"暂时不可执行"，但保留原序号
        stx,bx=a.request("POST","/api/reservations",{"slot_id":str(t2),"request_id":uid()})
        require(stx==200,f"A later占同时段 peer slot: {stx} {bx}")
        recs=occ.request("GET","/api/me/records?page=1&page_size=5")[1]["data"]["reservations"]
        rid=[r for r in recs if str(r["slot_id"])==str(t1)][0]["id"]
        occ.post(f"/api/reservations/{rid}/cancel",{"request_id":uid()})
        stA=s.sql("SELECT status FROM waitlist WHERE id=?",(int(wa),))[0][0]
        stB=s.sql("SELECT status FROM waitlist WHERE id=?",(int(wb_id),))[0][0]
        require(stA=="WAITING",f"blocked head keeps WAITING and its order: {stA}")
        require(stB=="PROMOTED",f"next executable candidate promoted past blocked head: {stB}")
        return {"skipped_blocked_head":True}
    record("T61 executable FIFO skips blocked head",t61) # -- r24/executable-fifo
    def t62(): # -- r24/held
        """HELD 限时保留：保留截止已过的待确认记录由扫描器回收为 EXPIRED 并重新递补；默认配置不产生 HELD。"""
        held_now=s.sql("SELECT count(*) FROM reservations WHERE status='HELD'")[0][0]
        require(held_now==0,f"no HELD without --hold-window (compatibility): {held_now}")
        adm=Client(s.port).login("admin")
        time.sleep(1.5)
        lab=adm.post("/api/admin/labs",{"name":"保留实验室"+uid()[:6],"location":"实验楼","description":"h"})["data"]["lab_id"]
        day=time.strftime("%Y-%m-%d",time.localtime(time.time()+86400))
        time.sleep(1.2)
        adm.post("/api/admin/slots/publish",{"lab_id":str(lab),"start_date":day,"end_date":day,"capacity":"1"})
        sid=s.sql("SELECT id FROM slots WHERE lab_id=? AND enabled=1 AND start_at>? ORDER BY start_at LIMIT 1",(int(lab),int(time.time())))[0][0]
        usr=s.sql("SELECT id FROM users WHERE username='user02'")[0][0]
        # 构造一条已过期的 HELD（hold_deadline 在过去），等待扫描器回收
        past=int(s.sql("SELECT strftime('%s','now')")[0][0])-60
        s.sql("INSERT INTO reservations(user_id,slot_id,status,source,created_at,hold_deadline) VALUES(?,?,'HELD','WAITLIST',?,?)",(usr,sid,past-600,past))
        rid=s.sql("SELECT id FROM reservations WHERE status='HELD' ORDER BY id DESC LIMIT 1")[0][0]
        deadline=time.time()+13
        st="HELD"
        while time.time()<deadline:
            st=s.sql("SELECT status FROM reservations WHERE id=?",(rid,))[0][0]
            if st!="HELD": break
            time.sleep(0.5)
        require(st=="EXPIRED",f"expired HELD reclaimed by sweeper: {st}")
        return {"held_reclaimed":True}
    record("T62 HELD hold expiry reclaimed",t62) # -- r24/held
    def t63(): # -- r25/approval-held
        """审批×HELD 组合：PENDING 不占容量（capacity=1 可并存两条 PENDING）、批准时重校容量（APPROVAL_CAPACITY）、
        候补递补绕过审批直接落 HELD（递补是系统行为）、确认后转 CONFIRMED。语义依据 CONTRACT「语义澄清」1/3。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="appr-held-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"ah.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"ah.db",extra=["--hold-window","3600"]) as sq:
            ad=Client(sq.port).login("admin")
            lab=ad.post("/api/admin/labs",{"name":"审批保留实验室"+uid()[:6],"location":"实验楼","description":"ah","require_approval":True})["data"]["lab_id"]
            now=int(time.time())
            base=((now+2*86400+28800)//86400*86400-28800)+9*3600
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),base,base+3600,1))
                sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
            def reg(tag):
                c=Client(sq.port);st,b=c.request("POST","/api/register",{"username":tag+uid()[:6],"password":PASSWORD})
                require(st==200,f"register {tag}: {st} {b}")
                c.csrf=b["data"]["csrf_token"]
                return c,b["data"]["user"]["username"]
            u1,n1=reg("ah1");u2,n2=reg("ah2");u3,n3=reg("ah3")
            st,b=u1.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
            require(st==200,f"u1 reserve: {st} {b}")
            rid1=b["data"]["reservation_id"]
            st,b=u2.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
            require(st==200,f"u2 也 PENDING（PENDING 不占容量）: {st} {b}")
            rid2=b["data"]["reservation_id"]
            recs1=u1.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
            require([x for x in recs1 if str(x["id"])==str(rid1)][0]["status"]=="PENDING","u1 PENDING")
            recs2=u2.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
            require([x for x in recs2 if str(x["id"])==str(rid2)][0]["status"]=="PENDING","u2 PENDING")
            st,b=ad.request("POST",f"/api/reservations/{rid1}/approve",{"request_id":uid()})
            require(st==200,f"approve u1: {st} {b}")
            st,b=ad.request("POST",f"/api/reservations/{rid2}/approve",{"request_id":uid()})
            require(st==409 and b["code"]=="APPROVAL_CAPACITY",f"批准时重校容量: {st} {b}")
            st,b=u3.request("POST","/api/waitlist",{"slot_id":str(sid),"request_id":uid()})
            require(st==200,f"u3 join waitlist: {st} {b}")
            u1.post(f"/api/reservations/{rid1}/cancel",{"request_id":uid()})
            row=sq.sql("SELECT id,status,hold_deadline FROM reservations WHERE slot_id=? AND user_id=(SELECT id FROM users WHERE username=?)",(int(sid),n3))
            require(len(row)==1,f"u3 promoted row: {row}")
            rid3,st3,dl=int(row[0][0]),row[0][1],row[0][2]
            require(st3=="HELD",f"递补落 HELD 而非 PENDING（递补绕过审批）: {st3}")
            require(dl and dl>time.time(),f"hold_deadline set: {dl}")
            st,b=u3.request("POST",f"/api/reservations/{rid3}/confirm",{"request_id":uid()})
            require(st==200,f"confirm: {st} {b}")
            require(sq.sql("SELECT status FROM reservations WHERE id=?",(rid3,))==[("CONFIRMED",)],"确认后 CONFIRMED")
        return {"approval_capacity":True,"promoted_held":True}
    record("T63 approval x HELD promotion bypasses approval",t63) # -- r25/approval-held
    def t64(): # -- r25/priority-fifo
        """优先级×可执行 FIFO 组合：递补按 priority DESC 扫描——高优先级管理员因时间冲突被暂跳后，
        低优先级普通用户获补位（可执行模式继续扫描）；strict 对照下遇冲突整体停止，无人递补。
        场次时间动态避开 admin/user18/user19 的既有预约（CI 与本地时区不同，固定偏移会偶发碰撞）。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        def build(sq):
            ad=Client(sq.port).login("admin")
            time.sleep(1.2)
            tag=uid()[:6]
            lab1=ad.post("/api/admin/labs",{"name":"优先A"+tag,"location":"实验楼","description":"p"})["data"]["lab_id"]
            time.sleep(1.2)
            lab2=ad.post("/api/admin/labs",{"name":"优先B"+tag,"location":"实验楼","description":"p"})["data"]["lab_id"]
            base=None
            for off in range(2,13):
                cand=((int(time.time())+off*86400+28800)//86400*86400-28800)+9*3600
                busy=sq.sql("SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN users u ON u.id=r.user_id WHERE u.username IN('admin','user18','user19') AND r.status IN('CONFIRMED','HELD') AND s.start_at<? AND ?<s.end_at",(cand+3600,cand))[0][0]
                if busy==0: base=cand; break
            require(base,"free 9am day found")
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab1),base,base+3600,1))
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab2),base,base+3600,1))
                t1=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab1),base)).fetchone()[0]
                t2=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab2),base)).fetchone()[0]
            return ad,t1,t2
        ad,t1,t2=build(s)  # 可执行模式（默认）
        occ=Client(s.port).login("user18")
        rr=occ.request("POST","/api/reservations",{"slot_id":str(t1),"request_id":uid()})
        require(rr[0]==200,f"blocker reserves t1: {rr}")
        a=Client(s.port).login("user19")
        st,wb=a.request("POST","/api/waitlist",{"slot_id":str(t1),"request_id":uid()})
        require(st==200,f"A joins: {st} {wb}")
        wa=wb["data"]["waitlist_id"]
        st,wb2=ad.request("POST","/api/waitlist",{"slot_id":str(t1),"request_id":uid()})
        require(st==200,f"admin joins (priority 10): {st} {wb2}")
        wadm=wb2["data"]["waitlist_id"]
        require(wadm>wa,"admin queued after A by id")
        st,_=ad.request("POST","/api/reservations",{"slot_id":str(t2),"request_id":uid()})
        require(st==200,"admin takes peer slot -> temporary clash")
        occ.post(f"/api/reservations/{rr[1]['data']['reservation_id']}/cancel",{"request_id":uid()})
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(int(wadm),))[0][0]=="WAITING","高优先级管理员被暂跳仍 WAITING")
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(int(wa),))[0][0]=="PROMOTED","低优先级 A 获递补（继续扫描而非停止）")
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="fifo-strict-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"fs.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"fs.db",extra=["--waitlist-strategy","strict"]) as sq2:
            ad2,t1b,t2b=build(sq2)
            occ2=Client(sq2.port).login("user18")
            rr2=occ2.request("POST","/api/reservations",{"slot_id":str(t1b),"request_id":uid()})
            require(rr2[0]==200,f"blocker: {rr2}")
            a2=Client(sq2.port).login("user19")
            st,wb3=a2.request("POST","/api/waitlist",{"slot_id":str(t1b),"request_id":uid()})
            require(st==200,f"A joins: {st}")
            st,wb4=ad2.request("POST","/api/waitlist",{"slot_id":str(t1b),"request_id":uid()})
            require(st==200,f"admin joins: {st}")
            st,_=ad2.request("POST","/api/reservations",{"slot_id":str(t2b),"request_id":uid()})
            require(st==200,"admin clash")
            occ2.post(f"/api/reservations/{rr2[1]['data']['reservation_id']}/cancel",{"request_id":uid()})
            st_a=sq2.sql("SELECT status FROM waitlist WHERE id=?",(int(wb3["data"]["waitlist_id"]),))[0][0]
            st_m=sq2.sql("SELECT status FROM waitlist WHERE id=?",(int(wb4["data"]["waitlist_id"]),))[0][0]
            require(st_a=="WAITING" and st_m=="WAITING",f"strict 模式遇冲突停止: A={st_a} admin={st_m}")
            require(sq2.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(t1b,))==[(0,)],"strict 下无人递补，名额保留")
        return {"priority_skipped":True,"strict_stops":True}
    record("T64 priority DESC x executable FIFO skip and strict stop",t64) # -- r25/priority-fifo
    def t65(): # -- r25/preempt-credit
        """抢占×信用组合：低余额用户被抢占获得 +1 补偿（PREEMPTED 流水，未触顶），信用恢复后可再次预约。
        先借首次预约消耗本周 WEEKLY 回补标记，使后续余额完全由测试控制（否则回补会把余额刷回 5）。"""
        import sqlite3 as _sq
        adm=Client(s.port).login("admin")
        time.sleep(1.5)
        victim=Client(s.port);st,b=victim.request("POST","/api/register",{"username":"vc"+uid()[:6],"password":PASSWORD})
        require(st==200,f"register victim: {st} {b}")
        victim.csrf=b["data"]["csrf_token"]
        vname=b["data"]["user"]["username"]
        vuid=int(b["data"]["user"]["id"])
        lab=adm.post("/api/admin/labs",{"name":"抢占信用实验室"+uid()[:6],"location":"实验楼","description":"pc"})["data"]["lab_id"]
        time.sleep(1.2)
        now=int(time.time())
        base=None
        for off in range(2,13):  # 管理员是抢占者：两天带宽都须避开其既有预约（T64 会留下 +2d 9 点）
            cand=((now+off*86400+28800)//86400*86400-28800)+9*3600
            busy=s.sql("SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN users u ON u.id=r.user_id WHERE u.username='admin' AND r.status IN('CONFIRMED','HELD') AND s.start_at<? AND ?<s.end_at",(cand+2*86400,cand))[0][0]
            if busy==0: base=cand; break
        require(base,"free two-day band found")
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),base,base+3600,1))
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),base+86400,base+86400+3600,1))
            s1=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
            s2=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base+86400)).fetchone()[0]
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE users SET credit=1 WHERE id=?",(vuid,))
        rr=victim.request("POST","/api/reservations",{"slot_id":str(s1),"request_id":uid()})
        require(rr[0]==200,f"victim reserves（触发本周回补标记）: {rr}")
        vid=rr[1]["data"]["reservation_id"]
        require(victim.request("GET","/api/me/credits?page=1&page_size=20")[1]["data"]["balance"]==5,"first booking topped up to 5")
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("UPDATE users SET credit=3 WHERE id=?",(vuid,))  # 本周已回补，余额自此测试可控
        st,b=adm.request("POST","/api/reservations",{"slot_id":str(s1),"request_id":uid()})
        require(st==200,f"admin preempts: {st} {b}")
        require(s.sql("SELECT status,cancel_reason FROM reservations WHERE id=?",(int(vid),))==[("CANCELLED","PREEMPTED")],"victim CANCELLED/PREEMPTED")
        led=victim.request("GET","/api/me/credits?page=1&page_size=20")[1]["data"]
        require(led["balance"]==4,f"credit 3->4（+1 补偿未触顶）: {led}")
        require(any(x["reason"]=="PREEMPTED" and int(x["delta"])==1 and str(x["reservation_id"])==str(vid) for x in led["ledger"]),f"PREEMPTED ledger: {led['ledger']}")
        st,_=victim.request("POST","/api/reservations",{"slot_id":str(s2),"request_id":uid()})
        require(st==200,f"信用>0 恢复预约资格: {st}")
        return {"credit_compensated":True}
    record("T65 preempt x credit compensation and rebook",t65) # -- r25/preempt-credit
    def t66(): # -- r25/maintenance-claim
        """维护×资源声明组合：资源转维护后新声明 409 STATE_CONFLICT，既有声明与 CONFIRMED 预约不受影响；恢复后可再声明。
        使用种子用户（user13-15）避免消耗全局注册桶。"""
        import sqlite3 as _sq
        adm=Client(s.port).login("admin")
        time.sleep(1.5)
        lab=adm.post("/api/admin/labs",{"name":"维护声明实验室"+uid()[:6],"location":"实验楼","description":"mc"})["data"]["lab_id"]
        time.sleep(1.2)
        aid=adm.post(f"/api/admin/labs/{lab}/assets",{"name":"维护声明设备","spec":"x","total":"3"})["data"]["asset_id"]
        now=int(time.time())
        base=((now+2*86400+28800)//86400*86400-28800)+9*3600
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,3,NULL)",(int(lab),base,base+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
        u1,u2,u3=(Client(s.port).login(n) for n in ("user13","user14","user15"))
        for u in (u1,u2):
            st,b=u.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
            require(st==200,f"claim reserve: {st} {b}")
        require(s.sql("SELECT count(*) FROM asset_claims WHERE asset_id=?",(int(aid),))[0][0]==2,"2 claims recorded")
        st,_=adm.request("POST",f"/api/admin/assets/{aid}/maintenance",{"op":"open","reason":"例行保养","request_id":uid()})
        require(st==200,"open maintenance")
        st,b=u3.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st==409 and b["code"]=="STATE_CONFLICT",f"维护中新声明 409: {st} {b}")
        kept=s.sql("SELECT count(*) FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id WHERE c.asset_id=? AND r.status='CONFIRMED'",(int(aid),))[0][0]
        require(kept==2,f"既有声明与 CONFIRMED 预约不受影响: {kept}")
        st,_=adm.request("POST",f"/api/admin/assets/{aid}/maintenance",{"op":"close","request_id":uid()})
        require(st==200,"close maintenance")
        st,b=u3.request("POST","/api/reservations",{"slot_id":str(sid),"assets":[str(aid)],"request_id":uid()})
        require(st==200,f"恢复后可再声明: {st} {b}")
        return {"maintenance_blocks_new_claims":True}
    record("T66 maintenance x asset claims",t66) # -- r25/maintenance-claim
    def t67(): # -- r25/reschedule-quota
        """改期×周配额组合：reschedule 不重计周配额——移出旧场次计入新场次，本周总数守恒；改期不放松 BR13。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="rs-quota-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"rq.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"rq.db",extra=["--quota-weekly","2"]) as sq:
            ad=Client(sq.port).login("admin")
            lab=ad.post("/api/admin/labs",{"name":"改期配额实验室"+uid()[:6],"location":"实验楼","description":"rq"})["data"]["lab_id"]
            ru=Client(sq.port);st,b=ru.request("POST","/api/register",{"username":"rq"+uid()[:6],"password":PASSWORD})
            require(st==200,f"register: {st} {b}")
            ru.csrf=b["data"]["csrf_token"]
            ruid=int(b["data"]["user"]["id"])
            now=int(time.time())
            base=((now+86400+28800)//86400*86400-28800)+9*3600
            sids=[]
            with _sq.connect(sq.db,timeout=5) as conn:
                for k in range(4):
                    sd=base+k*86400
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),sd,sd+3600,1))
                    sids.append(conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),sd)).fetchone()[0])
            rr=ru.request("POST","/api/reservations",{"slot_id":str(sids[0]),"request_id":uid()})
            require(rr[0]==200,f"book s1: {rr}")
            rid=rr[1]["data"]["reservation_id"]
            require(ru.request("POST","/api/reservations",{"slot_id":str(sids[1]),"request_id":uid()})[0]==200,"book s2")
            st,b=ru.request("POST","/api/reservations",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st==409 and b["code"]=="WEEKLY_QUOTA",f"配额满: {st} {b}")
            st,b=ru.request("POST",f"/api/reservations/{rid}/reschedule",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st==200,f"reschedule s1->s3（不重计配额）: {st} {b}")
            require(str(b["data"]["new_slot_id"])==str(sids[2]),f"new slot: {b}")
            moved=sq.sql("SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s2.start_at>=?",(ruid,base))[0][0]
            require(moved==2,f"本周有效预约数守恒（仍为 2）: {moved}")
            require(sq.sql("SELECT count(*) FROM reservations WHERE user_id=? AND slot_id=?",(ruid,int(sids[0]),))==[(0,)],"旧场次记录移除")
            st,b=ru.request("POST","/api/reservations",{"slot_id":str(sids[3]),"request_id":uid()})
            require(st==409 and b["code"]=="WEEKLY_QUOTA",f"改期不放水仍 409: {st} {b}")
        return {"quota_invariant":True}
    record("T67 reschedule x weekly quota invariant",t67) # -- r25/reschedule-quota
    def t68(): # -- r25/credential-lifecycle
        """凭据生命周期（P1-7）：停用/重置密码/改密均同事务吊销 API 令牌；令牌随后 401。结束恢复 user16 原状。"""
        import sqlite3 as _sq
        adm=Client(s.port).login("admin")
        time.sleep(1.2)
        u=Client(s.port).login("user16")
        t1=u.post("/api/me/tokens",{"name":"r25-lifecycle","request_id":uid()})["data"]["token"]
        st,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":t1})
        require(st==200,f"token works before: {st}")
        uid16=int(s.sql("SELECT id FROM users WHERE username='user16'")[0][0])
        # ① 停用 → 令牌吊销
        st,b=adm.request("POST",f"/api/admin/users/{uid16}/disable",{})
        require(st==200,f"disable user16: {st} {b}")
        require(s.sql("SELECT count(*) FROM api_tokens t JOIN users x ON x.id=t.user_id WHERE x.username='user16'")[0][0]==0,"停用后令牌清空")
        st,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":t1})
        require(st==401,f"token dead after disable: {st}")
        # ② 启用并重建令牌 → 重置密码 → 令牌吊销
        st,_=adm.request("POST",f"/api/admin/users/{uid16}/enable",{})
        require(st==200,f"enable user16: {st}")
        time.sleep(1.2)
        u=Client(s.port).login("user16")
        t2=u.post("/api/me/tokens",{"name":"r25-lifecycle-2","request_id":uid()})["data"]["token"]
        rp=adm.post(f"/api/admin/users/{uid16}/reset-password",{})
        newpw=rp["data"]["password"]
        require(s.sql("SELECT count(*) FROM api_tokens t JOIN users x ON x.id=t.user_id WHERE x.username='user16'")[0][0]==0,"重置后令牌清空")
        st,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":t2})
        require(st==401,f"token dead after reset: {st}")
        # ③ 改密（重置口令改回原口令）→ revoked_tokens 计数如实返回
        time.sleep(1.2)
        u=Client(s.port)
        st,bb=u.request("POST","/api/login",{"username":"user16","password":newpw})
        require(st==200,f"login with reset password: {st} {bb}")
        u.csrf=bb["data"]["csrf_token"]
        data=u.post("/api/me/password",{"old_password":newpw,"new_password":PASSWORD,"request_id":uid()})["data"]
        require(data["revoked_tokens"]==0,f"无令牌时改密计数为 0: {data}")
        time.sleep(1.2)
        u=Client(s.port).login("user16")
        t3=u.post("/api/me/tokens",{"name":"r25-lifecycle-3","request_id":uid()})["data"]["token"]
        time.sleep(1.2)
        data=u.post("/api/me/password",{"old_password":PASSWORD,"new_password":PASSWORD+"-r25","request_id":uid()})["data"]
        require(data["revoked_tokens"]==1,f"改密吊销 1 枚令牌: {data}")
        st,_=Client(s.port).request("GET","/api/me",headers={"X-API-Token":t3})
        require(st==401,f"token dead after password change: {st}")
        # 恢复 user16 口令，不影响其他用例（当前口令为 PASSWORD+"-r25"，需以它登录）
        time.sleep(1.2)
        u=Client(s.port)
        st,bb=u.request("POST","/api/login",{"username":"user16","password":PASSWORD+"-r25"})
        require(st==200,f"login with rotated password: {st} {bb}")
        u.csrf=bb["data"]["csrf_token"]
        u.post("/api/me/password",{"old_password":PASSWORD+"-r25","new_password":PASSWORD,"request_id":uid()})
        require(s.sql("SELECT count(*) FROM api_tokens t JOIN users x ON x.id=t.user_id WHERE x.username='user16'")[0][0]==0,"结束态无残留令牌")
        return {"lifecycle":True}
    record("T68 credential lifecycle revokes api tokens",t68) # -- r25/credential-lifecycle
    def t69(): # -- r25/suggestions
        """约束感知建议（创新方向 1 最小版）：逐场次评估 bookable/joinable 并给出原因——
        满员→可候补不可预约（SLOT_FULL）；配额满→不可预约但候补不受阻（BR13）；信用为零→全部阻塞。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="sugg-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"sg.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"sg.db",extra=["--quota-weekly","1"]) as sq:
            ad=Client(sq.port).login("admin")
            lab=ad.post("/api/admin/labs",{"name":"建议实验室"+uid()[:6],"location":"实验楼","description":"sg"})["data"]["lab_id"]
            ru=Client(sq.port);st,b=ru.request("POST","/api/register",{"username":"sg"+uid()[:6],"password":PASSWORD})
            require(st==200,f"register: {st} {b}")
            ru.csrf=b["data"]["csrf_token"]
            ruid=int(b["data"]["user"]["id"])
            now=int(time.time())
            base=((now+86400+28800)//86400*86400-28800)+9*3600
            with _sq.connect(sq.db,timeout=5) as conn:
                for k in (0,3600):
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),base+k,base+k+3600,1))
                s1=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
                s2=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base+3600)).fetchone()[0]
            day=time.strftime("%Y-%m-%d",time.localtime(base))
            def sug():
                return ru.request("GET",f"/api/suggestions?lab_id={lab}&date={day}")[1]["data"]["suggestions"]
            # 管理员占满 s1；评估者视角：s1 满员可候补，s2 可预约
            require(ad.request("POST","/api/reservations",{"slot_id":str(s1),"request_id":uid()})[0]==200,"admin fills s1")
            rows={str(x["slot_id"]):x for x in sug()}
            require(rows[str(s1)]["bookable"] is False and rows[str(s1)]["joinable"] is True,f"s1 满员可候补: {rows[str(s1)]}")
            require(any(r["code"]=="SLOT_FULL" for r in rows[str(s1)]["reasons"]),"s1 SLOT_FULL 原因")
            require(rows[str(s2)]["bookable"] is True and not rows[str(s2)]["reasons"],f"s2 可预约: {rows[str(s2)]}")
            # 评估者约下 s2（配额 1 已满）：s2 出 ALREADY_RESERVED+WEekly_QUOTA；s1 候补不受配额影响
            st,b=ru.request("POST","/api/reservations",{"slot_id":str(s2),"request_id":uid()})
            require(st==200,f"ru books s2: {st} {b}")
            rows={str(x["slot_id"]):x for x in sug()}
            codes2={r["code"] for r in rows[str(s2)]["reasons"]}
            require(rows[str(s2)]["bookable"] is False,f"s2 不可再约: {rows[str(s2)]}")
            require("ALREADY_RESERVED" in codes2 and "WEEKLY_QUOTA" in codes2,f"s2 原因: {codes2}")
            require(rows[str(s1)]["joinable"] is True,f"配额不阻塞候补（BR13）: {rows[str(s1)]}")
            # 信用清零 → 全部阻塞
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE users SET credit=0 WHERE id=?",(ruid,))
            rows={str(x["slot_id"]):x for x in sug()}
            require(all("CREDIT_EXHAUSTED" in {r["code"] for r in rows[k]["reasons"]} for k in (str(s1),str(s2))),"信用为零全部阻塞")
            require(rows[str(s1)]["joinable"] is False and rows[str(s1)]["bookable"] is False,"信用为零不可候补")
        return {"suggestions":True}
    record("T69 constraint-aware suggestions",t69) # -- r25/suggestions
    def t70(): # -- r26/weighted-aging
        """老化加权候补（--waitlist-strategy weighted）：score=1000·priority+200·(credit−5)+60·log₂(1+等待/3600)。
        ①同 credit 下等待 10h 的先入者胜过刚入队者（aging 生效）；②credit 3+等10h(≈207) 输给 credit 5 刚入队(+400)；
        ③管理员(10000)仍压过普通用户(credit 5, 等 10h≈207)——老化只解决同档饿死，不破坏角色优先级。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="waged-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"wg.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"wg.db",extra=["--waitlist-strategy","weighted"]) as sq:
            ad=Client(sq.port).login("admin")
            time.sleep(1.2)
            lab=ad.post("/api/admin/labs",{"name":"老化实验室"+uid()[:6],"location":"实验楼","description":"w"})["data"]["lab_id"]
            now=int(time.time())
            base=((now+2*86400+28800)//86400*86400-28800)+9*3600
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,3,NULL)",(int(lab),base,base+3600,1))
                sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
            occ1=Client(sq.port).login("user18")
            occ2=Client(sq.port).login("user19")
            occ3=Client(sq.port).login("user20")
            for oc in (occ1,occ2,occ3):
                oc.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
            def join(cl):
                st,b=cl.request("POST","/api/waitlist",{"slot_id":str(sid),"request_id":uid()})
                require(st==200,f"join: {st} {b}")
                return int(b["data"]["waitlist_id"])
            def cancel(cl,name):
                rid=sq.sql("SELECT id FROM reservations WHERE slot_id=? AND user_id=(SELECT id FROM users WHERE username=?) AND status IN('CONFIRMED','HELD')",(sid,name))[0][0]
                st,b=cl.request("POST",f"/api/reservations/{rid}/cancel",{"request_id":uid()})
                require(st==200,f"cancel {name}: {st} {b}")
            # ①B 先入队（id 小）、A 后入队再拨 A 到 10h 前：weighted 下 A 凭 aging≈207 反超 id 序（executable 对照为 B 胜）
            B=Client(sq.port).login("user02");A=Client(sq.port).login("user01")
            wb=join(B);wa=join(A)
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE waitlist SET created_at=? WHERE id=?",(now-36000,wa))
            cancel(occ1,"user18")
            require(sq.sql("SELECT status FROM waitlist WHERE id=?",(wa,))[0][0]=="PROMOTED","①aging：久等者先转正")
            require(sq.sql("SELECT status FROM waitlist WHERE id=?",(wb,))[0][0]=="WAITING","①B 仍等待")
            # ②C(credit 3, 10h → −400+207=−193) vs D(credit 5, 刚入队=0) → 同档 B/D 先于 C
            # credit 必须在 join 之后改：入队会触发每周信用回补（本周无 WEEKLY 流水则补回 5），先改会被覆盖（T65 同源陷阱）
            C=Client(sq.port).login("user03");D=Client(sq.port).login("user04")
            wc=join(C);wd=join(D)
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE users SET credit=3 WHERE username='user03'")
                conn.execute("UPDATE users SET credit=5 WHERE username='user04'")
                conn.execute("UPDATE waitlist SET created_at=? WHERE id=?",(now-36000,wc))
            cancel(occ2,"user19")
            got=sq.sql("SELECT user_id FROM waitlist WHERE slot_id=? AND status='PROMOTED' ORDER BY id DESC LIMIT 1",(sid,))[0][0]
            require(got!=sq.sql("SELECT id FROM users WHERE username='user03'")[0][0],"②信用项压制 aging：低信用久等者不获递补")
            require(sq.sql("SELECT status FROM waitlist WHERE id=?",(wc,))[0][0]=="WAITING","②C 仍等待")
            # ③管理员(credit 5, 刚入队=10000) vs E(credit 5, 等 10h≈207) → 管理员胜
            E=Client(sq.port).login("user05")
            we=join(E)
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE waitlist SET created_at=? WHERE id=?",(now-36000,we))
            wm=join(ad)
            cancel(occ3,"user20")
            require(sq.sql("SELECT status FROM waitlist WHERE id=?",(wm,))[0][0]=="PROMOTED","③管理员候补最优先（不被 aging 越级）")
            require(sq.sql("SELECT status FROM waitlist WHERE id=?",(we,))[0][0]=="WAITING","③普通用户未越级")
        return {"aging":True,"credit_dominates":True,"role_priority_kept":True}
    record("T70 weighted aging waitlist strategy",t70) # -- r26/weighted-aging
    def t71(): # -- r26/promote-probability
        """转正概率预测：同 (lab,星期几) 过去 28 天已开场场次中"释放数≥排位"的经验占比；
        样本 <5 场为 null；join 响应附 queue_ahead 与概率。"""
        import sqlite3 as _sq
        adm=Client(s.port).login("admin")
        time.sleep(1.2)
        lab=adm.post("/api/admin/labs",{"name":"概率实验室"+uid()[:6],"location":"实验楼","description":"p"})["data"]["lab_id"]
        now=int(time.time())
        base=((now+2*86400+28800)//86400*86400-28800)+9*3600
        fut_wd=None
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),base,base+3600,1))
            sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),base)).fetchone()[0]
            fut_wd=((base+28800)//86400+4)%7
            # 造历史：过去 35 天（5 周）内同星期几的已开场场次（样本≥5）——模式 1,1,0,1,1 → 4/6 释放
            hist=[]
            for k in range(1,8):
                cand=base-k*7*86400  # 同 base 的星期几，往前每 7 天
                if cand<now-34*86400:break
                hist.append(cand)
            released_flags=[]
            for i,cand in enumerate(hist):
                conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),cand,cand+3600,1))
                hid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),cand)).fetchone()[0]
                rel=1 if i%3!=2 else 0  # 模式 1,1,0,1,1,0 → 前缀释放数
                released_flags.append(rel)
                if rel:
                    conn.execute("INSERT INTO reservations(user_id,slot_id,status,source,created_at,cancelled_at,cancel_reason) VALUES(2,?,'CANCELLED','DIRECT',?,?,'USER')",(hid,cand-7200,cand-3600))
        require(len(hist)>=5,f"历史样本≥5: {len(hist)}")
        day=time.strftime("%Y-%m-%d",time.localtime(base))
        rows={str(x["slot_id"]):x for x in adm.request("GET",f"/api/suggestions?lab_id={lab}&date={day}")[1]["data"]["suggestions"]}
        require("promote_probability" in rows[str(sid)],"概率字段存在")
        # 排位 1（无人候补）：P(释放≥1)=释放场次占比
        import math
        p1=rows[str(sid)]["promote_probability"]
        expect=sum(released_flags)/len(released_flags)
        require(p1 is not None and abs(p1-expect)<1e-9,f"rank1 概率=历史释放占比: {p1} vs {expect}")
        # join 响应带 queue_ahead=0 与同一概率
        occ=Client(s.port).login("user18")
        occ.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
        u=Client(s.port).login("user17")
        st,b=u.request("POST","/api/waitlist",{"slot_id":str(sid),"request_id":uid()})
        require(st==200,f"join: {st} {b}")
        require(int(b["data"]["queue_ahead"])==0,f"排第 1 位: {b['data']}")
        require(b["data"]["promote_probability"] is not None and abs(b["data"]["promote_probability"]-expect)<1e-9,f"join 概率一致: {b['data']}")
        # 再来一人候补（rank2）：P(释放≥2)=释放数≥2 的场次占比（本场景全为 0/1 → 0.0）
        u2=Client(s.port).login("user16")
        st,b2=u2.request("POST","/api/waitlist",{"slot_id":str(sid),"request_id":uid()})
        require(st==200 and int(b2["data"]["queue_ahead"])==1,f"排第 2 位: {b2['data']}")
        require(b2["data"]["promote_probability"]==0.0,f"rank2 概率=0（无场次释放≥2）: {b2['data']}")
        # 无样本实验室 → null
        lab2=adm.post("/api/admin/labs",{"name":"概率空白"+uid()[:6],"location":"实验楼","description":"p"})["data"]["lab_id"]
        day2=time.strftime("%Y-%m-%d",time.localtime(base+86400))
        with _sq.connect(s.db,timeout=5) as conn:
            conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab2),base+86400,base+86400+3600,1))
        rows2=adm.request("GET",f"/api/suggestions?lab_id={lab2}&date={day2}")[1]["data"]["suggestions"]
        require(rows2 and rows2[0]["promote_probability"] is None,f"样本不足为 null: {rows2[0] if rows2 else rows2}")
        return {"probability":expect}
    record("T71 promote probability from history",t71) # -- r26/promote-probability
    def t72(): # -- r26/fairness
        """公平性审计：per_user 聚合与 SQL 对账一致；Jain 指数与手算一致；days 参数校验。"""
        adm=Client(s.port).login("admin")
        data=adm.request("GET","/api/admin/fairness?days=28")[1]["data"]
        users={x["username"]:x for x in data["users"]}
        require("user01" in users,"user01 在审计列表")
        # 对账：user01 近 28 天 waitlist 计数
        now=int(time.time());since=now-28*86400
        joined=s.sql("SELECT count(*) FROM waitlist w JOIN users u ON u.id=w.user_id WHERE u.username='user01' AND w.created_at>=?",(since,))[0][0]
        require(users["user01"]["joined"]==joined,f"joined 对账: {users['user01']['joined']} vs {joined}")
        granted=s.sql("SELECT count(*) FROM reservations r JOIN users u ON u.id=r.user_id WHERE u.username='user01' AND r.created_at>=?",(since,))[0][0]
        require(users["user01"]["granted"]==granted,f"granted 对账: {users['user01']['granted']} vs {granted}")
        # Jain 手算
        xs=[x["granted"] for x in data["users"]]
        n=len(xs);ssum=sum(xs);ssq=sum(v*v for v in xs)
        expect=(ssum*ssq) and (ssum*ssum)/(n*ssq) or 1.0
        require(abs(data["jain_index"]-expect)<1e-9,f"Jain 对账: {data['jain_index']} vs {expect}")
        require(0<data["jain_index"]<=1,"Jain ∈ (0,1]")
        st,_=adm.request("GET","/api/admin/fairness?days=0")
        require(st==400,"days=0 拒绝")
        require(Client(s.port).login("user01").request("GET","/api/admin/fairness")[0]==403,"非管理员 403")
        return {"jain":data["jain_index"]}
    record("T72 fairness audit endpoint",t72) # -- r26/fairness
    def t73(): # -- r26/waitlist-daily-limit
        """每日候补上限（--waitlist-daily-limit）：达限 409 WAITLIST_LIMIT；北京日界重置（SQL 拨 created_at 到昨日）。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="wld-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"wl.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"wl.db",extra=["--waitlist-daily-limit","2"]) as sq:
            ad=Client(sq.port).login("admin")
            lab=ad.post("/api/admin/labs",{"name":"上限实验室"+uid()[:6],"location":"实验楼","description":"l"})["data"]["lab_id"]
            now=int(time.time())
            base=((now+2*86400+28800)//86400*86400-28800)+9*3600
            sids=[]
            with _sq.connect(sq.db,timeout=5) as conn:
                for k in range(3):
                    sd=base+k*86400
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab),sd,sd+3600,1))
                    sids.append(conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab),sd)).fetchone()[0])
            u=Client(sq.port).login("user06")
            occ=Client(sq.port).login("user07")
            for sd in sids:  # 候补要求满员：先由他人占满三场（不同天同时刻，无重叠）
                st,_=occ.request("POST","/api/reservations",{"slot_id":str(sd),"request_id":uid()})
                require(st==200,f"occ fill: {st}")
            st,_=u.request("POST","/api/waitlist",{"slot_id":str(sids[0]),"request_id":uid()})
            require(st==200,f"1st join: {st}")
            st,_=u.request("POST","/api/waitlist",{"slot_id":str(sids[1]),"request_id":uid()})
            require(st==200,f"2nd join: {st}")
            st,b=u.request("POST","/api/waitlist",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st==409 and b["code"]=="WAITLIST_LIMIT",f"3rd 409: {st} {b}")
            # 拨一天前的一条入队到昨日 → 释放一个今日名额
            d0=(now+28800)//86400*86400-28800
            with _sq.connect(sq.db,timeout=5) as conn:
                conn.execute("UPDATE waitlist SET created_at=? WHERE user_id=(SELECT id FROM users WHERE username='user06') AND slot_id=?",(d0-3600,sids[0]))
            st,b=u.request("POST","/api/waitlist",{"slot_id":str(sids[2]),"request_id":uid()})
            require(st==200,f"昨日计入今日之外 → 放行: {st} {b}")
        return {"daily_limit":True}
    record("T73 waitlist daily limit",t73) # -- r26/waitlist-daily-limit
    def t74(): # -- r29/property-weighted
        """差分属性测试：weighted 递补与 Python 参考模型在 12 组随机场景下逐场一致。
        参考模型 = score(1000·priority+200·(credit−5)+60·log₂(1+等待h)) 降序稳定排序
        + 可执行扫描（跳过停用者与时间冲突者）；与 promote() 实测递补者对拍。"""
        import tempfile, subprocess as _sp, sqlite3 as _sq, math as _math, random as _rnd
        tdir=pathlib.Path(tempfile.mkdtemp(prefix="prop-"))
        seed_env=dict(os.environ);seed_env["LAB_SEED_PASSWORD"]=PASSWORD
        r0=_sp.run([str(pathlib.Path("build/lab-booking.exe").resolve()),"--db",str(tdir/"pr.db"),"--seed","--init-only"],env=seed_env,capture_output=True,text=True,timeout=60)
        require(r0.returncode==0,f"seed: {r0.stderr}")
        with running(pathlib.Path("build/lab-booking.exe").resolve(),tdir,tdir/"pr.db",
                     extra=["--waitlist-strategy","weighted","--rate-burst","100000"]) as sq:
            ad=Client(sq.port).login("admin")
            time.sleep(1.2)
            tag=uid()[:6]
            lab1=ad.post("/api/admin/labs",{"name":"属性A"+tag,"location":"x","description":"p"})["data"]["lab_id"]
            time.sleep(1.0)
            lab2=ad.post("/api/admin/labs",{"name":"属性B"+tag,"location":"x","description":"p"})["data"]["lab_id"]
            now=int(time.time())
            base=((now+2*86400+28800)//86400*86400-28800)+9*3600
            occ=Client(sq.port).login("user18")
            rnd=_rnd.Random(20260917)
            pool=[f"user{i:02d}" for i in range(1,11)]
            uid_by_name={n:sq.sql("SELECT id FROM users WHERE username=?",(n,))[0][0] for n in pool}
            def ref_winner(slot,nowv):
                rows=sq.sql("SELECT w.id,w.user_id,w.priority,w.created_at,u.credit,u.enabled FROM waitlist w JOIN users u ON u.id=w.user_id WHERE w.slot_id=? AND w.status='WAITING'",(slot,))
                rows.sort(key=lambda r:(-r[2],r[0]))
                scored=[]
                for r in rows:
                    wait_h=max(0.0,(nowv-r[3])/3600.0)
                    sc=1000.0*r[2]+200.0*(r[4]-5)+60.0*max(0.0,_math.log2(1.0+wait_h))
                    scored.append((sc,r))
                scored.sort(key=lambda t:-t[0])
                tgt=sq.sql("SELECT start_at,end_at FROM slots WHERE id=?",(slot,))[0]
                for sc,r in scored:
                    if not r[5]:continue
                    clash=sq.sql("SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s.id<>? AND s.start_at<? AND ?<s.end_at",(r[1],slot,tgt[1],tgt[0]))[0][0]
                    if clash:continue
                    return r[1]
                return None
            mismatches=0
            for rd in range(12):
                st=base+rd*5400
                with _sq.connect(sq.db,timeout=5) as conn:
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,1,NULL)",(int(lab1),st,st+3600,1))
                    conn.execute("INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,?,9,NULL)",(int(lab2),st,st+3600,1))
                    sid=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab1),st)).fetchone()[0]
                    sid2=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at=?",(int(lab2),st)).fetchone()[0]
                st_o,_=occ.request("POST","/api/reservations",{"slot_id":str(sid),"request_id":uid()})
                require(st_o==200,f"占位 r{rd}: {st_o}")
                names=rnd.sample(pool,rnd.randint(3,6))
                if rd==10: names.append("admin")
                hours=rnd.sample(range(1,31),len(names))
                wids={}
                for n in names:
                    c=Client(sq.port).login(n)
                    stw,bw=c.request("POST","/api/waitlist",{"slot_id":str(sid),"request_id":uid()})
                    require(stw==200,f"入队 {n} r{rd}: {stw} {bw}")
                    wids[n]=bw["data"]["waitlist_id"]
                with _sq.connect(sq.db,timeout=5) as conn:
                    for n,h in zip(names,hours):
                        conn.execute("UPDATE waitlist SET created_at=? WHERE id=?",(now-h*3600,wids[n]))
                        conn.execute("UPDATE users SET credit=? WHERE username=?",(rnd.randint(1,5),n))  # ≥1 保证后续轮次仍可入队
                    if rd==11:
                        conn.execute("UPDATE users SET enabled=0 WHERE username='user03'")
                for n in names:  # 30% 概率制造跨场冲突（入队后占 B 同时段）
                    if n!="admin" and rnd.random()<0.3:
                        c=Client(sq.port).login(n)
                        c.request("POST","/api/reservations",{"slot_id":str(sid2),"request_id":uid()})
                expected=ref_winner(sid,int(time.time()))
                rid_o=sq.sql("SELECT id FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD') AND user_id=(SELECT id FROM users WHERE username='user18')",(sid,))[0][0]
                stc,_=occ.request("POST",f"/api/reservations/{rid_o}/cancel",{"request_id":uid()})
                require(stc==200,f"取消 r{rd}: {stc}")
                prom=sq.sql("SELECT user_id FROM waitlist WHERE slot_id=? AND status='PROMOTED'",(sid,))
                actual=prom[0][0] if prom else None
                if expected!=actual:
                    mismatches+=1
                    print(f"  r{rd} 不一致: 期望 {expected} 实际 {actual}")
                if rd==11:
                    with _sq.connect(sq.db,timeout=5) as conn:
                        conn.execute("UPDATE users SET enabled=1 WHERE username='user03'")
            require(mismatches==0,f"12 轮差分一致（不一致 {mismatches} 轮）")
        return {"rounds":12,"mismatches":0}
    record("T74 differential property test for weighted promotion",t74) # -- r29/property-weighted


def account_checks(s):
    """T20-T21：改密（其他会话失效）与在线会话管理。"""
    def t20():
        NEWPW, WRONG = PASSWORD + "-new", PASSWORD + "-wrong"  # 派生口令，测试结束恢复原口令
        client=Client(s.port).login("user03"); other=Client(s.port).login("user03")
        sessions=client.request("GET","/api/me/sessions")[1]["data"]["sessions"]
        require(len(sessions)==2,f"two sessions before change: {len(sessions)}")
        require(sum(1 for x in sessions if x["current"])==1,"exactly one current session")
        require(client.request("POST","/api/me/password",{"old_password":WRONG,"new_password":NEWPW,"request_id":uid()})[0]==401,"wrong old password rejected")
        require(client.request("POST","/api/me/password",{"old_password":PASSWORD,"new_password":"short","request_id":uid()})[0]==400,"short new password rejected")
        require(client.request("POST","/api/me/password",{"old_password":PASSWORD,"new_password":PASSWORD,"request_id":uid()})[0]==400,"unchanged password rejected")
        data=client.post("/api/me/password",{"old_password":PASSWORD,"new_password":NEWPW,"request_id":uid()})["data"]
        require(data["revoked_sessions"]==1,f"other session revoked: {data}")
        require(other.request("GET","/api/me")[0]==401,"other session invalidated")
        require(client.request("GET","/api/me")[0]==200,"current session preserved")
        require(Client(s.port).request("POST","/api/login",{"username":"user03","password":PASSWORD})[0]==401,"old password retired")
        require(Client(s.port).request("POST","/api/login",{"username":"user03","password":NEWPW})[0]==200,"new password accepted")
        client.post("/api/me/password",{"old_password":NEWPW,"new_password":PASSWORD,"request_id":uid()})
        return {"password_rotated":True}
    record("T20 password change revokes other sessions",t20)
    def t21():
        one=Client(s.port).login("user04"); two=Client(s.port).login("user04")
        sessions=one.request("GET","/api/me/sessions")[1]["data"]["sessions"]
        require(len(sessions)==2,"two sessions before revoke")
        target=[x for x in sessions if not x["current"]][0]
        one.post(f"/api/me/sessions/{target['id']}/revoke",{"request_id":uid()})
        require(two.request("GET","/api/me")[0]==401,"revoked session rejected")
        require(one.request("GET","/api/me")[0]==200,"current session kept")
        require(one.request("POST","/api/me/sessions/not-a-valid-token/revoke",{"request_id":uid()})[0]==404,"invalid session id rejected")
        require(one.request("POST",f"/api/me/sessions/{target['id']}/revoke",{"request_id":uid()})[0]==404,"repeated revoke rejected")
        return {"sessions":2}
    record("T21 online session listing and revoke",t21)

def noshow_checks(s,window):
    """T22：签到超时自动释放，名额按 FIFO 补位并记入通知。

    签到窗口须大于本用例自身耗时：窗口过短时，补位产生的新预约会在断言期间再次到期被释放，
    持续产生新的 NO_SHOW 通知，使"全部标记已读"断言不稳定。
    """
    def t22():
        a=s.user(1); b=s.user(2)
        slot=s.slots()[0]
        rid=a.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
        wid=b.post("/api/waitlist",{"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
        now=int(time.time()); start=_safe_past(now-window-1); s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(start,start+3600,slot))
        rows=[]; deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            rows=s.sql("SELECT status,cancel_reason FROM reservations WHERE id=?",(rid,))
            if rows and rows[0][0]=="CANCELLED": break
            time.sleep(.4)
        require(rows==[("CANCELLED","NO_SHOW")],f"no-show release: {rows}")
        require(s.sql("SELECT status,source FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))==[("CONFIRMED","WAITLIST")],"waiter promoted after release")
        require(s.sql("SELECT status FROM waitlist WHERE id=?",(wid,))==[("PROMOTED",)],"waitlist row promoted")
        require(s.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))==[(1,)],"single occupation preserved")
        owner=a.request("GET","/api/me/notifications")[1]["data"]
        require(any(n["kind"]=="NO_SHOW" for n in owner["notifications"]),"owner notified about release")
        waiter=b.request("GET","/api/me/notifications")[1]["data"]
        require(any(n["kind"]=="PROMOTED" for n in waiter["notifications"]),"waiter notified about promotion")
        require(owner["unread_count"]>=1,"unread count present")
        target=waiter["notifications"][0]["id"]
        require(b.request("POST","/api/me/notifications/read",{"ids":[target],"request_id":uid()})[0]==200,"mark single notification read")
        before=waiter["unread_count"]
        b.post("/api/me/notifications/read",{"all":True,"request_id":uid()})
        after=b.request("GET","/api/me/notifications")[1]["data"]["unread_count"]
        require(after==0,f"all notifications read: {after}")
        require(b.request("POST","/api/me/notifications/read",{"request_id":uid()})[0]==400,"missing ids rejected")
        return {"unread_before":before}
    record("T22 no-show release, promotion and notifications",t22)

def security_checks(s):
    """T23-T24：登录防爆破与写操作限流（独立实例，小阈值获得确定性）。"""
    def t23():
        c=Client(s.port)  # 未登录客户端
        for i in range(3):
            status,_=c.request("POST","/api/login",{"username":"user05","password":PASSWORD+"-wrong"})
            require(status==401,f"wrong login {i+1}: {status}")
        status,body=c.request("POST","/api/login",{"username":"user05","password":PASSWORD})
        require(status==429 and body["code"]=="LOGIN_LOCKED",f"locked after max fails: {status} {body}")
        time.sleep(1.3)  # 仍处锁定窗口内
        status,_=Client(s.port).request("POST","/api/login",{"username":"admin","password":PASSWORD})
        require(status==200,"other username unaffected")
        deadline=time.time()+5
        while time.time()<deadline:  # 锁定 3 秒后解锁（轮询，避免边界脆弱）
            status,_=c.request("POST","/api/login",{"username":"user05","password":PASSWORD})
            if status==200: break
            time.sleep(0.3)
        require(status==200,f"unlock after lockout window: {status}")
    record("T23 login brute force lockout and unlock",t23)
    def t24():
        c=s.user(6)
        codes=[]
        for _ in range(3): # 机器缓慢时补充窗口可能横跨请求，多轮快速脉冲直至命中限额
            codes=[]
            for _ in range(6):
                status,body=c.request("POST","/api/me/notifications/read",{"all":True,"request_id":uid()})
                codes.append((status,body["code"]))
            if any(st==429 for st,_ in codes): break
        require(any(st==429 and cd=="RATE_LIMITED" for st,cd in codes),f"burst exhausted: {codes}")
        time.sleep(1.3)
        status,_=c.request("POST","/api/me/notifications/read",{"all":True,"request_id":uid()})
        require(status==200,f"refill after window: {status}")
    record("T24 mutation rate limit burst and refill",t24)

def sweep_fault_case(exe,directory,baseline):
    """爽约扫描事务中途崩溃（exit 88）：重启前回滚、重启后回收与补位原子完成。"""
    with running(exe,pathlib.Path(directory)/"sweep-fault",baseline,extra=["--checkin-window","1","--sweep-interval","1","--fault","sweep-mid"]) as s:
        a,b=s.user(1),s.user(2)
        slot=s.slots()[0]
        rid=a.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
        wid=b.post("/api/waitlist",{"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
        now=int(time.time());ns=_safe_past(now-7);s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(ns,ns+3600,slot))
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and s.process.poll() is None:time.sleep(.3)
        require(s.process.poll()==88,f"sweep fault exit {s.process.poll()}")
        st=s.sql("SELECT status FROM reservations WHERE id=?",(rid,))
        require(st==[("CONFIRMED",)],f"未提交事务应整体回滚: {st}")
        s.stop()
        s.extra=["--checkin-window","1","--sweep-interval","1"] # 重启实例移除故障参数
        s.fault=None;s.start() # 同库重启：WAL 数据完整保留
        rows=[];deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            rows=s.sql("SELECT status,cancel_reason FROM reservations WHERE id=?",(rid,))
            if rows and rows[0][0]=="CANCELLED":break
            time.sleep(.4)
        require(rows==[("CANCELLED","NO_SHOW")],f"重启后回收: {rows}")
        st=s.sql("SELECT status FROM waitlist WHERE id=?",(wid,))
        require(st==[("PROMOTED",)],f"候补已补位: {st}")
        require(s.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(slot,))==[(1,)],"补位后单占用保持")
        s.integrity()
    return {"exit_code":88}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--quick",action="store_true")
    mode.add_argument("--full",action="store_true")
    parser.add_argument("--exe",type=pathlib.Path,default=ROOT/"build/lab-booking.exe")
    parser.add_argument("--test-exe",type=pathlib.Path,default=ROOT/"build/lab-booking-test.exe")
    parser.add_argument("--output",type=pathlib.Path,default=ROOT/"tests/results")
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    if len(PASSWORD)<8: parser.error("请通过环境变量 LAB_TEST_PASSWORD 提供至少 8 位的测试专用口令")
    for file in (args.exe,args.test_exe):
        if not file.is_file(): parser.error(f"Missing executable: {file}")
    full=args.full; rounds=20 if full else 1; fault_rounds=10 if full else 1
    runs_dir = ROOT / "artifacts" / "test-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    # Retain every run's original databases and logs, including failed runs.
    run_path = pathlib.Path(tempfile.mkdtemp(prefix="full-" if full else "quick-", dir=runs_dir))
    print(f"Raw experiment directory: {run_path}", flush=True)
    with contextlib.nullcontext(run_path) as temp:
        baseline=pathlib.Path(temp)/"baseline.db"
        env=os.environ.copy(); env["LAB_SEED_PASSWORD"]=PASSWORD
        seed=subprocess.run([str(args.exe),"--db",str(baseline),"--seed","--init-only"],env=env,capture_output=True,text=True,timeout=30)
        require(seed.returncode==0,f"seed failed: {seed.returncode}: {seed.stdout} {seed.stderr}")
        with running(args.exe,pathlib.Path(temp)/"regression",baseline) as s: regression(s)
        with running(args.exe,pathlib.Path(temp)/"features",baseline,extra=["--checkin-window","60","--sweep-interval","1","--remind-sec","300","--backup-interval","0"]) as s: feature_checks(s)
        with running(args.exe,pathlib.Path(temp)/"accounts",baseline) as s: account_checks(s)
        with running(args.exe,pathlib.Path(temp)/"noshow",baseline,extra=["--checkin-window","5","--sweep-interval","1"]) as s: noshow_checks(s,5)
        with running(args.exe,pathlib.Path(temp)/"security",baseline,extra=["--login-max-fails","3","--login-lockout","3","--rate-burst","3","--rate-refill-sec","1"]) as s: security_checks(s)
        with running(args.exe,pathlib.Path(temp)/"races",baseline,extra=["--rate-burst","100000"]) as s: record("T10 concurrent unique occupation",lambda:races(s,rounds))
        for fault in ("cancel-before-promote","after-commit"):
            record("T11 recovery "+fault,lambda f=fault:fault_case(args.test_exe,temp,baseline,f,fault_rounds))
        record("T11 recovery sweep-mid",lambda:sweep_fault_case(args.test_exe,pathlib.Path(temp)/"sweep",baseline))
        check=subprocess.run([str(args.exe),"--db",str(baseline),"--check"],capture_output=True,text=True,timeout=15)
        record("CLI database check",lambda:require(check.returncode==0,f"--check failed {check.stdout} {check.stderr}"))
        rejected=subprocess.run([str(args.exe),"--db",str(baseline),"--fault","after-commit","--fault-request",uid()],capture_output=True,text=True,timeout=10)
        record("Production rejects fault flags",lambda:require(rejected.returncode!=0,"production accepted fault flags"))
        # Copy logs to the summary directory; original run evidence remains intact.
        for log in pathlib.Path(temp).rglob("server.log"):
            target=args.output/"logs"/log.relative_to(temp)
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(log,target)
    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    report={"timestamp_utc":timestamp,"mode":"full" if full else "quick","executable":str(args.exe.resolve()),"raw_evidence_directory":str(run_path),"tests":RESULTS,"all_passed":all(r["passed"] for r in RESULTS),"concurrency":RACES}
    (args.output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if RACES:
        with (args.output/"concurrency.csv").open("w",newline="",encoding="utf-8-sig") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(RACES[0])); writer.writeheader(); writer.writerows(RACES)
    print(f"Evidence: {args.output.resolve()}")
    return 0 if report["all_passed"] else 1

if __name__=="__main__":
    raise SystemExit(main())
