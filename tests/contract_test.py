#!/usr/bin/env python3
"""契约形状测试（黑盒）：逐端点验证 docs/CONTRACT.md 承诺的封套与字段类型。

每个成功/失败响应都必须是 {code,message,data} 三键封套；本文档化的端点
逐一核对其 data 字段名与类型（编号为字符串、布尔为 JSON bool、分页字段、
v1.1 新增 capacity/confirmed_count 等），防止实现漂移破坏前端依赖。
"""
from __future__ import annotations
import datetime as dt
import os, pathlib, sys, time, uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

ENVELOPE = {"code", "message", "data"}

def check_envelope(status, body, where):
    require(isinstance(body, dict), f"{where}: body not object")
    require(set(body.keys()) == ENVELOPE, f"{where}: envelope keys {sorted(body.keys())}")
    require(isinstance(body["code"], str) and body["code"], f"{where}: code type")
    require(isinstance(body["message"], str), f"{where}: message type")
    require(isinstance(body["data"], dict), f"{where}: data type")

def is_id(v):  # 所有对外编号统一为十进制字符串
    return isinstance(v, str) and v.isdigit() and len(v) <= 18

def is_date(v):
    try:
        dt.date.fromisoformat(v); return True
    except (ValueError, TypeError):
        return False

def main():
    run_dir = ROOT / "tests" / "results" / f"contract-{uid()[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = run_dir / "baseline.db"
    env = dict(os.environ); env["LAB_SEED_PASSWORD"] = PASSWORD
    import subprocess
    r = subprocess.run([str(ROOT / "build" / "lab-booking.exe"), "--db", str(baseline),
                        "--seed", "--init-only"], env=env, capture_output=True, text=True, timeout=30)
    require(r.returncode == 0, f"seed failed: {r.stderr}")
    server = Server(ROOT / "build" / "lab-booking.exe", run_dir / "srv", baseline,
                    extra=["--rate-burst", "100000"])
    server.start()
    try:
        admin = Client(server.port).login("admin")
        user = Client(server.port).login("user01")
        anon = Client(server.port)
        checks = 0

        # --- 认证与档案 ---
        st, b = anon.request("GET", "/api/health")
        check_envelope(st, b, "health"); checks += 1
        d = b["data"]
        require(d["status"] == "ok" and isinstance(d.get("version"), str)
                and isinstance(d.get("uptime_s"), int) and d["uptime_s"] >= 0, f"health fields: {d}")
        st, b = anon.request("GET", "/api/me")
        check_envelope(st, b, "me anon"); checks += 1
        require(st == 401 and b["code"] == "UNAUTHORIZED", f"anon me: {st} {b['code']}")
        st, b = anon.request("GET", "/api/admin/metrics")
        require(st == 401, f"anon metrics: {st}"); checks += 1
        st, b = user.request("GET", "/api/admin/metrics")
        require(st == 403 and b["code"] == "FORBIDDEN", f"user metrics: {st} {b['code']}"); checks += 1

        # --- 实验室列表：字段名与类型逐一核对 ---
        st, b = user.request("GET", "/api/labs")
        check_envelope(st, b, "labs"); checks += 1
        labs = b["data"]["labs"]
        require(isinstance(labs, list) and labs, "labs non-empty")
        for lab in labs:
            require(set(lab.keys()) == {"id", "name", "location", "description", "enabled"},
                    f"lab keys: {sorted(lab.keys())}")
            require(is_id(lab["id"]) and isinstance(lab["enabled"], bool), f"lab types: {lab}")
        lab_id = labs[0]["id"]

        # --- 场次列表：v1.1 契约（capacity/confirmed_count，无 occupied）---
        day = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=40)).strftime("%Y-%m-%d")
        admin.post("/api/admin/slots/publish", {"lab_id": lab_id, "start_date": day, "end_date": day, "capacity": 200})
        st, b = user.request("GET", f"/api/slots?lab_id={lab_id}&date={day}")
        check_envelope(st, b, "slots"); checks += 1
        slots = b["data"]["slots"]
        require(isinstance(b["data"]["checkin_window"], int) and b["data"]["checkin_window"] > 0,
                "checkin_window type")
        require(slots, "published slots visible")
        for s in slots:
            expect = {"id", "lab_id", "start_at", "end_at", "enabled", "lab_enabled",
                      "capacity", "confirmed_count", "waiting_count",
                      "my_reservation_id", "my_checked_in_at", "my_waitlist_id"}
            require(set(s.keys()) == expect, f"slot keys: {sorted(s.keys())}")
            require(is_id(s["id"]) and 1 <= int(s["capacity"]) <= 200, f"slot capacity: {s}")
            require(isinstance(s["confirmed_count"], int) and isinstance(s["waiting_count"], int),
                    f"slot counts: {s}")
            for nullable in ("my_reservation_id", "my_checked_in_at", "my_waitlist_id"):
                require(s[nullable] is None or is_id(s[nullable]), f"{nullable}: {s[nullable]}")
            require(s["end_at"] - s["start_at"] == 3600, "slot is one hour")
        slot_id = slots[0]["id"]

        # --- 我的记录：分页封套 ---
        st, b = user.request("GET", "/api/me/records?page=1&page_size=5")
        check_envelope(st, b, "records"); checks += 1
        d = b["data"]
        require({"reservations", "waitlist", "events", "page", "page_size", "has_more"} <= set(d.keys()),
                f"records keys: {sorted(d.keys())}")
        require(d["page"] == 1 and d["page_size"] == 5 and isinstance(d["has_more"], bool),
                f"paging: {d['page']},{d['page_size']},{d['has_more']}")
        st, b = user.request("GET", "/api/me/records?page=0")
        require(st == 400, f"page=0: {st}"); checks += 1
        st, b = user.request("GET", "/api/me/records?page_size=201")
        require(st == 400, f"page_size=201: {st}"); checks += 1

        # --- 管理统计与导出 ---
        st, b = admin.request("GET", f"/api/admin/stats?start_date={day}&end_date={day}")
        check_envelope(st, b, "stats"); checks += 1
        keys = {"date", "slots", "confirmed", "cancelled", "no_show", "checked_in", "waiting"}
        for row in b["data"]["stats"]:
            require(set(row.keys()) == keys and is_date(row["date"]), f"stats row: {row}")
        require(set(b["data"]["totals"].keys()) == keys - {"date"}, f"totals: {b['data']['totals']}")
        st, b = admin.request("GET", f"/api/admin/stats/export?start_date={day}&end_date={day}")
        check_envelope(st, b, "export"); checks += 1
        require(b["data"]["filename"].endswith(".csv") and b["data"]["content"].startswith("﻿"),
                "csv BOM + filename")

        # --- 通知/会话/指标 ---
        st, b = user.request("GET", "/api/me/notifications?unread=1")
        check_envelope(st, b, "notifications"); checks += 1
        require({"notifications", "unread_count", "page", "page_size", "has_more"} <= set(b["data"].keys()),
                "notifications envelope")
        st, b = user.request("GET", "/api/me/sessions")
        check_envelope(st, b, "sessions"); checks += 1
        sess = b["data"]["sessions"]
        require(sess and all(isinstance(x["current"], bool) and len(x["id"]) == 64 for x in sess),
                f"sessions: {sess[:1]}")
        st, b = admin.request("GET", "/api/admin/metrics")
        check_envelope(st, b, "metrics"); checks += 1
        c = b["data"]["counters"]
        require({"requests_total", "ok_2xx", "err_4xx", "err_5xx", "db_busy_503", "logins"} <= set(c.keys()),
                f"counters: {sorted(c.keys())}")
        require(c["requests_total"] == c["ok_2xx"] + c["err_4xx"] + c["err_5xx"],
                f"counter invariant: {c}")
        require({"count", "sum", "max", "buckets"} <= set(b["data"]["latency_ms"].keys()), "latency keys")

        # --- 错误路径封套一致性 ---
        st, b = anon.request("GET", "/api/no-such-endpoint")
        check_envelope(st, b, "401"); checks += 1
        require(st == 401 and b["code"] == "UNAUTHORIZED", f"anon unknown: {st} {b['code']}")
        st, b = user.request("GET", "/api/no-such-endpoint")
        check_envelope(st, b, "404"); checks += 1
        require(st == 404, f"unknown endpoint: {st}")
        st, b = anon.request("GET", "/api/login")
        check_envelope(st, b, "405"); checks += 1
        require(st == 405 and b["code"] == "METHOD_NOT_ALLOWED", f"wrong method: {st} {b['code']}")

        # --- 预约/取消往返：响应字段契约 ---
        r = user.post("/api/reservations", {"slot_id": slot_id, "request_id": uid()})
        rid = r["data"]["reservation_id"]
        require(is_id(rid), f"reservation_id: {rid}"); checks += 1
        r = user.post(f"/api/reservations/{rid}/cancel", {"request_id": uid()})
        require(is_id(r["data"]["reservation_id"]) and r["data"]["promoted_reservation_id"] is None,
                f"cancel shape: {r['data']}"); checks += 1

        print(f"[PASS] contract: {checks} endpoint checks, envelope+types verified")
        return 0
    finally:
        server.stop()

if __name__ == "__main__":
    raise SystemExit(main())
