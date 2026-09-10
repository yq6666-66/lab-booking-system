"""Independent HTTP/SQLite integration experiments; Python standard library only."""
from __future__ import annotations
import argparse, concurrent.futures, contextlib, csv, datetime, http.client, json
import os, pathlib, shutil, socket, sqlite3, statistics, subprocess, tempfile, threading, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
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
        return [str(row[0]) for row in self.sql("SELECT id FROM slots WHERE start_at > ? AND enabled=1 ORDER BY start_at,id", (int(time.time())+3600,))]

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
        s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(int(time.time())-7200,int(time.time())-3600,slot))
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
    record("T12 SQLite integrity and relational invariants",s.integrity)
    def t15():
        c=s.user(4)
        s.sql("UPDATE sessions SET expires_at=1")
        require(c.request("GET","/api/me")[0]==401,"expired session accepted")
        s.user(4)
        total,valid=s.sql("SELECT (SELECT count(*) FROM sessions),(SELECT count(*) FROM sessions WHERE expires_at>?)",(int(time.time()),))[0]
        require(total==valid and valid>=1,"expired sessions pruned after login")
    record("T15 expired session rejected and pruned",t15)

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

def bj_today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

def feature_checks(s):
    """T16-T19：签到、签到边界、记录分页、统计导出（使用较宽的签到窗口）。"""
    admin=s.user(0); a=s.user(1); b=s.user(2)
    pools=iter(s.slots())
    def reserve(client,slot): return client.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
    def rewind(slot,offset=-1):
        now=int(time.time()); s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(now+offset,now+offset+3600,slot))
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

def noshow_checks(s):
    """T22：签到超时自动释放，名额按 FIFO 补位并记入通知（极短签到窗口）。"""
    def t22():
        a=s.user(1); b=s.user(2)
        slot=s.slots()[0]
        rid=a.post("/api/reservations",{"slot_id":slot,"request_id":uid()})["data"]["reservation_id"]
        wid=b.post("/api/waitlist",{"slot_id":slot,"request_id":uid()})["data"]["waitlist_id"]
        now=int(time.time()); s.sql("UPDATE slots SET start_at=?,end_at=? WHERE id=?",(now-1,now+3599,slot))
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
        status,_=Client(s.port).request("POST","/api/login",{"username":"admin","password":PASSWORD})
        require(status==200,"other username unaffected")
        time.sleep(1.3)
        status,_=c.request("POST","/api/login",{"username":"user05","password":PASSWORD})
        require(status==200,f"unlock after lockout window: {status}")
    record("T23 login brute force lockout and unlock",t23)
    def t24():
        c=s.user(6)
        codes=[]
        for _ in range(4):
            status,body=c.request("POST","/api/me/notifications/read",{"all":True,"request_id":uid()})
            codes.append((status,body["code"]))
        require(all(st==200 for st,_ in codes[:3]),f"first burst allowed: {codes}")
        require(codes[3][0]==429 and codes[3][1]=="RATE_LIMITED",f"4th mutation limited: {codes}")
        time.sleep(1.3)
        status,_=c.request("POST","/api/me/notifications/read",{"all":True,"request_id":uid()})
        require(status==200,f"refill after window: {status}")
    record("T24 mutation rate limit burst and refill",t24)

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
        with running(args.exe,pathlib.Path(temp)/"features",baseline,extra=["--checkin-window","60","--sweep-interval","1"]) as s: feature_checks(s)
        with running(args.exe,pathlib.Path(temp)/"accounts",baseline) as s: account_checks(s)
        with running(args.exe,pathlib.Path(temp)/"noshow",baseline,extra=["--checkin-window","1","--sweep-interval","1"]) as s: noshow_checks(s)
        with running(args.exe,pathlib.Path(temp)/"security",baseline,extra=["--login-max-fails","3","--login-lockout","1","--rate-burst","3","--rate-refill-sec","1"]) as s: security_checks(s)
        with running(args.exe,pathlib.Path(temp)/"races",baseline,extra=["--rate-burst","100000"]) as s: record("T10 concurrent unique occupation",lambda:races(s,rounds))
        for fault in ("cancel-before-promote","after-commit"):
            record("T11 recovery "+fault,lambda f=fault:fault_case(args.test_exe,temp,baseline,f,fault_rounds))
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
