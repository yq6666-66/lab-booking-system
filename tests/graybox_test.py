#!/usr/bin/env python3
"""灰盒测试：经 API 驱动业务动作，再直接查询 SQLite 验证数据落库正确性。

白盒（unit.c）验证内部函数、黑盒（integration.py）只看 HTTP 响应；
灰盒取两者之间：不读 C 源码、但持有数据库视图，验证「API 承诺的行为
确实以正确的形态写入了存储层」，可发现响应正确但落库错误的实现漂移。
"""
from __future__ import annotations
import datetime as dt
import os, pathlib, sqlite3, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

def main():
    run_dir = ROOT / "tests" / "results" / f"graybox-{uid()[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = run_dir / "baseline.db"
    env = dict(os.environ); env["LAB_SEED_PASSWORD"] = PASSWORD
    r = subprocess.run([str(ROOT / "build" / "lab-booking.exe"), "--db", str(baseline),
                        "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=30)
    require(r.returncode == 0, f"seed failed: {r.stderr}")
    server = Server(ROOT / "build" / "lab-booking.exe", run_dir / "srv", baseline,
                    extra=["--rate-burst", "100000"])
    server.start()
    try:
        admin = Client(server.port).login("admin")

        # G1 注册 → users 表：角色 USER、argon2id 哈希、启用状态
        uname = f"gb{uid()[:8]}"
        c = Client(server.port)
        st, b = c.request("POST", "/api/register", {"username": uname, "password": PASSWORD})
        require(st == 200, f"register: {st}")
        c.csrf = b["data"]["csrf_token"]  # 注册即登录，但 Client 只在 login() 里记录 csrf
        rows = server.sql("SELECT id,role,enabled,password_hash FROM users WHERE username=?", (uname,))
        require(len(rows) == 1, "exactly one user row")
        uid_num, role, enabled, phash = rows[0]
        require(role == "USER" and enabled == 1, f"role/enabled: {role},{enabled}")
        require(str(phash).startswith("$argon2id$"), f"argon2id hash: {str(phash)[:12]}")
        print("G1 register -> users row ok")

        # G2 预约 → reservations CONFIRMED/DIRECT + operation_events RESERVE 带同一 request_id
        day = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=40)).strftime("%Y-%m-%d")
        admin.post("/api/admin/slots/publish", {"lab_id": "1", "start_date": day, "end_date": day})
        slots = Client(server.port).login("user01").request("GET", f"/api/slots?lab_id=1&date={day}")[1]["data"]["slots"]
        slot = slots[0]["id"]
        req = uid()
        c.post("/api/reservations", {"slot_id": slot, "request_id": req})
        rows = server.sql("SELECT id,status,source FROM reservations WHERE user_id=? AND slot_id=?", (uid_num, int(slot)))
        require(len(rows) == 1 and rows[0][1] == "CONFIRMED" and rows[0][2] == "DIRECT", f"reservation row: {rows}")
        ev = server.sql("SELECT count(*) FROM operation_events WHERE action='RESERVE' AND request_id=?", (req,))[0][0]
        require(ev == 1, f"RESERVE event: {ev}")
        print("G2 reserve -> reservations+event ok")

        # G3 同 request_id 重放 → 不产生第二条预约行（幂等落库）
        st1, b1 = c.request("POST", "/api/reservations", {"slot_id": slot, "request_id": req})
        require(st1 == 200, f"replay status: {st1}")
        n = server.sql("SELECT count(*) FROM reservations WHERE user_id=? AND slot_id=?", (uid_num, int(slot)))[0][0]
        require(n == 1, f"replay created extra row: {n}")
        rc = server.sql("SELECT count(*) FROM request_receipts WHERE user_id=? AND request_id=?", (uid_num, req))[0][0]
        require(rc == 1, f"receipt rows: {rc}")
        print("G3 replay idempotent ok")

        # G4 满员+候补+取消 → 补位预约 source=WAITLIST、候补行 PROMOTED、被补位者收到 PROMOTED 通知
        cap_slot = slots[1]["id"]
        server.sql("UPDATE slots SET capacity=1 WHERE id=?", (int(cap_slot),))
        c.post("/api/reservations", {"slot_id": cap_slot, "request_id": uid()})
        c2 = Client(server.port).login("user02")
        st, _ = c2.request("POST", "/api/reservations", {"slot_id": cap_slot, "request_id": uid()})
        require(st == 409, f"full: {st}")
        st, _ = c2.request("POST", "/api/waitlist", {"slot_id": cap_slot, "request_id": uid()})
        require(st == 200, "waitlist join")
        wl = server.sql("SELECT id,status FROM waitlist WHERE user_id=(SELECT id FROM users WHERE username='user02') AND slot_id=?", (int(cap_slot),))
        require(len(wl) == 1 and wl[0][1] == "WAITING", f"waitlist row: {wl}")
        my_rid = server.sql("SELECT id FROM reservations WHERE user_id=? AND slot_id=? AND status='CONFIRMED'", (uid_num, int(cap_slot)))[0][0]
        c.post(f"/api/reservations/{my_rid}/cancel", {"request_id": uid()})
        wrow = server.sql("SELECT status FROM waitlist WHERE id=?", (wl[0][0],))[0][0]
        require(wrow == "PROMOTED", f"waitlist after cancel: {wrow}")
        src = server.sql("SELECT source FROM reservations WHERE user_id=(SELECT id FROM users WHERE username='user02') AND slot_id=? AND status='CONFIRMED'", (int(cap_slot),))[0][0]
        require(src == "WAITLIST", f"promoted source: {src}")
        note = server.sql("SELECT count(*) FROM notifications n JOIN users u ON u.id=n.user_id WHERE u.username='user02' AND n.kind='PROMOTED'")[0][0]
        require(note >= 1, "PROMOTED notification written")
        print("G4 cancel->promote->notify ok")

        # G5 场次未开始时签到被拒，DB 侧不得出现 checked_in_at
        u2id = server.sql("SELECT id FROM users WHERE username=?", ("user02",))[0][0]
        promoted_rid = server.sql("SELECT id FROM reservations WHERE user_id=? AND slot_id=? AND status='CONFIRMED'", (u2id, int(cap_slot)))[0][0]
        st, b = c2.request("POST", f"/api/reservations/{promoted_rid}/checkin", {"request_id": uid()})
        require(st == 409 and b["code"] == "STATE_CONFLICT", f"premature checkin: {st} {b['code']}")
        rows = server.sql("SELECT checked_in_at FROM reservations WHERE slot_id=? AND status='CONFIRMED'", (int(cap_slot),))
        require(all(x[0] is None for x in rows), "no premature checkin in db")
        print("G5 premature checkin blocked ok")

        # G6 改密 → password_hash 更新、其他会话行被删、当前会话保留
        c3 = Client(server.port).login(uname)
        old_hash = server.sql("SELECT password_hash FROM users WHERE id=?", (uid_num,))[0][0]
        st, b = c3.request("POST", "/api/me/password", {"old_password": PASSWORD, "new_password": PASSWORD + "-New9", "request_id": uid()})
        require(st == 200, f"password change: {st} {b}")
        new_hash = server.sql("SELECT password_hash FROM users WHERE id=?", (uid_num,))[0][0]
        require(new_hash != old_hash and str(new_hash).startswith("$argon2id$"), "hash rotated")
        sess = server.sql("SELECT count(*) FROM sessions WHERE user_id=?", (uid_num,))[0][0]
        require(sess == 1, f"sessions after change: {sess}")  # 仅当前会话保留
        print("G6 password rotate ok")

        # G7 审计链完整性：一次请求可产生多条事件（如 cancel 附带 PROMOTE），
        # 但同 (actor,action,entity,request_id) 四元组不得重复；回执表主键唯一
        dup = server.sql("SELECT count(*) FROM (SELECT actor_id,action,entity_id,request_id FROM operation_events WHERE request_id IS NOT NULL GROUP BY actor_id,action,entity_id,request_id HAVING count(*)>1)")[0][0]
        require(dup == 0, "duplicate audit quadruple")
        rc_total, rc_dist = server.sql("SELECT count(*),count(DISTINCT user_id||':'||request_id) FROM request_receipts")[0]
        require(rc_total == rc_dist, f"receipt key not unique: {rc_total}/{rc_dist}")
        print("G7 audit uniqueness ok")

        print(f"[PASS] graybox: 7 scenarios, API<->DB cross-verified ({run_dir.name})")
        return 0
    finally:
        server.stop()

if __name__ == "__main__":
    raise SystemExit(main())
