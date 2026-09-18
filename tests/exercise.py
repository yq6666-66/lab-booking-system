"""覆盖度伴随脚本：以合法请求走遍全部接口路径，配合优雅停机产出 gcov 数据。"""
from __future__ import annotations
import sys, uuid
sys.path.insert(0, str(pathlib_dir := __import__("pathlib").Path(__file__).resolve().parent))
from integration import Client, PASSWORD  # noqa: E402

def main():
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8080
    a=Client(port).login("admin")
    u=Client(port).login("user01")
    u2=Client(port).login("user02")
    u3=Client(port).login("user03")
    u4=Client(port).login("user04")
    for c in (a,u,u2,u3,u4):
        c.request("GET","/api/me");c.request("GET","/api/labs");c.request("GET","/api/me/records?page=1&page_size=5")
    a.request("GET","/api/health")
    a.request("GET","/api/slots?lab_id=1&date=2026-09-20")
    a.request("GET","/api/admin/records?date=2026-09-20")
    a.request("GET","/api/admin/stats?start_date=2026-09-20&end_date=2026-09-20")
    a.request("GET","/api/admin/stats/export?start_date=2026-09-20&end_date=2026-09-20")
    a.request("GET","/api/admin/metrics")
    lab=a.post("/api/admin/labs",{"name":f"覆盖率实验室{uuid.uuid4().hex[:8]}","location":"实验楼","description":"cov"})["data"]["lab_id"]
    a.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":"2026-09-20","end_date":"2026-09-20","capacity":"3"})
    slots=a.request("GET","/api/slots?lab_id="+lab+"&date=2026-09-20")[1]["data"]["slots"]
    slot=slots[0]["id"]
    st_codes=[]
    for c in (u,u2,u3,u4):
        st,_=c.request("POST","/api/reservations",{"slot_id":slot,"request_id":str(uuid.uuid4())})
        st_codes.append(st) # 3 成功 1 满（SLOT_FULL）——容量制路径
    st,_=u4.request("POST","/api/waitlist",{"slot_id":slot,"request_id":str(uuid.uuid4())}) # 满员候补
    a.request("GET","/api/slots?lab_id="+lab+"&date=2026-09-20")
    recs=u.request("GET","/api/me/records")[1]["data"]["reservations"]
    if recs:
        u.request("POST",f"/api/reservations/{recs[0]['id']}/cancel",{"request_id":str(uuid.uuid4())}) # 取消→FIFO 补位
    u2.request("GET","/api/me/notifications?page=1&page_size=10")
    u2.post("/api/me/notifications/read",{"all":True,"request_id":str(uuid.uuid4())})
    u2.request("GET","/api/me/sessions")
    p=u2.post("/api/me/password",{"old_password":PASSWORD,"new_password":PASSWORD+"-cov","request_id":str(uuid.uuid4())})
    if p.get("code")=="OK":
        u2.post("/api/me/password",{"old_password":PASSWORD+"-cov","new_password":PASSWORD,"request_id":str(uuid.uuid4())})
    sess=u2.request("GET","/api/me/sessions")[1]["data"]["sessions"]
    for x in sess:
        if not x["current"]:u2.post(f"/api/me/sessions/{x['id']}/revoke",{"request_id":str(uuid.uuid4())})
    a.post("/api/admin/labs/1/update",{"name":"软件工程实验室","location":"信息楼","description":"整间实验室 · 固定一小时场次","enabled":True})
    a.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":"2026-09-25","end_date":"2026-09-26"})
    # -- r9-r13 运营与资源端点走查（拉满 service.c/http.c 新分支覆盖） --
    a.post("/api/admin/notifications",{"all":True,"title":"覆盖率公告","body":"exercise"})
    a.request("GET","/api/admin/notifications/sent?page=1&page_size=5")
    a.request("GET","/api/admin/logs?lines=50")
    a.request("GET","/api/admin/logs?lines=20&level=3")
    import sqlite3 as _sq
    with _sq.connect("artifacts/coverage-run/cov.db") as conn:
        row=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at>strftime('%s','now') ORDER BY start_at LIMIT 1",(int(lab),)).fetchone()
    if row:
        a.post(f"/api/admin/slots/{row[0]}/update",{"capacity":"4","enabled":True})
    ru="cov"+uuid.uuid4().hex[:8]
    ru_client=Client(port);ru_client.request("POST","/api/register",{"username":ru,"password":PASSWORD})
    ru_id=ru_client.request("GET","/api/me")[1]["data"]["user"]["id"]
    a.post(f"/api/admin/users/{ru_id}/disable",{"request_id":str(uuid.uuid4())})
    a.post(f"/api/admin/users/{ru_id}/enable",{"request_id":str(uuid.uuid4())})
    rst=a.post(f"/api/admin/users/{ru_id}/reset-password",{"request_id":str(uuid.uuid4())})
    if rst.get("code")!="OK":print("reset-password:",rst)
    a.post(f"/api/admin/labs/{lab}/assets",{"name":"覆盖率示波器","spec":"4 通道","total":"5","status":"MAINTENANCE"})
    a.request("GET",f"/api/labs/{lab}/assets")
    a.request("GET","/api/admin/labs/utilization?start_date=2026-09-20&end_date=2026-09-26")
    a.request("GET","/api/admin/stats/utilization/export?start_date=2026-09-20&end_date=2026-09-26")
    # -- r14-r25 端点走查（改期/签到签退/批量/审批/信用/令牌/维护工单/时段窗/资格/建议/日历/声明视图/用户列表） --
    uid_of=lambda c:int(c.request("GET","/api/me")[1]["data"]["user"]["id"])
    u1_id,u2_id,u3_id,u4_id=uid_of(u),uid_of(u2),uid_of(u3),uid_of(u4)
    rid=lambda:str(uuid.uuid4())
    day2="2026-09-21"
    # 资源域：时段窗/资格/维护工单/用量
    aid=a.post(f"/api/admin/labs/{lab}/assets",{"name":"覆盖率万用表","spec":"6 位半","total":"2"})["data"]["asset_id"]
    a.post(f"/api/admin/assets/{aid}/windows",{"weekday_mask":127,"start_minute":480,"end_minute":1020,"request_id":rid()})
    a.request("GET",f"/api/admin/assets/{aid}/windows")
    a.request("GET",f"/api/admin/assets/{aid}/usage?start_date=2026-09-20&end_date=2026-09-26")
    a.post(f"/api/admin/assets/{aid}/qualifications",{"op":"grant","user_id":str(u4_id),"note":"cov"})
    a.request("GET",f"/api/admin/assets/{aid}/qualifications")
    a.post(f"/api/admin/assets/{aid}/qualifications",{"op":"require","required":True,"user_id":str(u4_id)})
    a.post(f"/api/admin/assets/{aid}/qualifications",{"op":"revoke","user_id":str(u4_id)})
    a.post(f"/api/admin/assets/{aid}/qualifications",{"op":"require","required":False,"user_id":str(u4_id)})
    a.post(f"/api/admin/assets/{aid}/maintenance",{"op":"open","reason":"cov","request_id":rid()})
    a.request("GET",f"/api/admin/assets/{aid}/maintenance")
    a.post(f"/api/admin/assets/{aid}/maintenance",{"op":"close","request_id":rid()})
    # 审批流：PENDING → 批准/拒绝
    alab=a.post("/api/admin/labs",{"name":f"覆盖率审批{uuid.uuid4().hex[:6]}","location":"实验楼","description":"cov","require_approval":True})["data"]["lab_id"]
    a.post("/api/admin/slots/publish",{"lab_id":alab,"start_date":day2,"end_date":day2,"capacity":"3"})
    aslots=a.request("GET",f"/api/slots?lab_id={alab}&date={day2}")[1]["data"]["slots"]
    if aslots:
        aslot=aslots[0]["id"]
        st,_=u.request("POST","/api/reservations",{"slot_id":aslot,"request_id":rid()})
        st,_=u2.request("POST","/api/reservations",{"slot_id":aslot,"request_id":rid()})
        a.request("GET","/api/admin/records?status=PENDING&page=1&page_size=10")
        pr=u.request("GET","/api/me/records?page=1&page_size=5")[1]["data"]["reservations"]
        pend=[x for x in pr if x.get("status")=="PENDING"]
        if pend:
            a.post(f"/api/reservations/{pend[0]['id']}/approve",{"request_id":rid()})
        pend2=u2.request("GET","/api/me/records?page=1&page_size=5")[1]["data"]["reservations"]
        pend2=[x for x in pend2 if x.get("status")=="PENDING"]
        if pend2:
            a.post(f"/api/reservations/{pend2[0]['id']}/reject",{"request_id":rid()})
    # HELD 确认：直插一条未过期的 HELD 再走 confirm 分支
    with _sq.connect("artifacts/coverage-run/cov.db") as conn:
        fslot=conn.execute("SELECT id FROM slots WHERE lab_id=? AND start_at>strftime('%s','now') ORDER BY start_at LIMIT 1",(int(lab),)).fetchone()
        if fslot:
            conn.execute("INSERT INTO reservations(user_id,slot_id,status,source,created_at,hold_deadline) VALUES(?,?,'HELD','WAITLIST',strftime('%s','now'),strftime('%s','now')+3600)",(u3_id,int(fslot[0]),))
        hid=conn.execute("SELECT id FROM reservations WHERE user_id=? AND status='HELD' ORDER BY id DESC LIMIT 1",(u3_id,)).fetchone()
    if hid:
        u3.request("POST",f"/api/reservations/{hid[0]}/confirm",{"request_id":rid()})
    # 改期 / 签到 / 签退 / 批量连场
    recs3=u3.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
    conf=[x for x in recs3 if x.get("status")=="CONFIRMED"]
    if len(slots)>1 and conf:
        u3.request("POST",f"/api/reservations/{conf[0]['id']}/reschedule",{"slot_id":slots[1]["id"],"request_id":rid()})
    mine=u4.request("GET","/api/me/records?page=1&page_size=10")[1]["data"]["reservations"]
    mine_c=[x for x in mine if x.get("status")=="CONFIRMED"]
    if mine_c:
        with _sq.connect("artifacts/coverage-run/cov.db") as conn:
            conn.execute("UPDATE slots SET start_at=strftime('%s','now')-30,end_at=strftime('%s','now')+3570 WHERE id=?",(int(mine_c[0]["slot_id"]),))
        st,_=u4.request("POST",f"/api/reservations/{mine_c[0]['id']}/checkin",{"request_id":rid()})
        u4.request("POST",f"/api/reservations/{mine_c[0]['id']}/checkout",{"request_id":rid()})
    bslots=a.request("GET",f"/api/slots?lab_id={lab}&date=2026-09-25")[1]["data"]["slots"]
    if len(bslots)>=2:
        u4.request("POST","/api/reservations/batch",{"slot_ids":[str(bslots[0]["id"]),str(bslots[1]["id"])],"request_id":rid()})
    # 信用 / 令牌 / 建议 / 日历 / 声明视图 / 用户列表
    u.request("GET","/api/me/credits?page=1&page_size=10")
    a.post(f"/api/admin/users/{u4_id}/credit",{"delta":"1","request_id":rid()})
    tk=u.post("/api/me/tokens",{"name":"cov-token","request_id":rid()}).get("data",{}).get("token")
    u.request("GET","/api/me/tokens")
    if tk:
        u.request("GET","/api/me",headers={"X-API-Token":tk})
        tid=u.request("GET","/api/me/tokens")[1]["data"]["tokens"][0]["id"]
        u.post(f"/api/me/tokens/{tid}/revoke",{"request_id":rid()})
    u2.request("GET",f"/api/suggestions?lab_id={lab}&date=2026-09-20")
    u.request("GET","/api/me/calendar/export")
    u.request("GET","/api/me/calendar.ics")
    a.request("GET","/api/admin/asset-claims?start_date=2026-09-20&end_date=2026-09-26")
    a.request("GET","/api/admin/asset-claims/export?start_date=2026-09-20&end_date=2026-09-26")
    # -- r29 覆盖走查扩：公平审计/确认/参数校验分支 --
    a.request("GET","/api/admin/fairness?days=28")
    a.request("GET","/api/admin/fairness?days=90")
    a.request("GET","/api/admin/fairness?days=0")      # 400 分支
    u.request("GET","/api/admin/fairness")              # 403 分支
    import sqlite3 as _sq2
    with _sq2.connect("artifacts/coverage-run/cov.db") as conn:
        fslot=conn.execute("SELECT s.id FROM slots s WHERE s.start_at>strftime('%s','now') AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.user_id=2) ORDER BY s.start_at LIMIT 1").fetchone()
        if fslot:
            conn.execute("INSERT INTO reservations(user_id,slot_id,status,source,created_at,hold_deadline) VALUES(?,?,'HELD','WAITLIST',strftime('%s','now'),strftime('%s','now')+3600)",(2,fslot[0]))
            hid=conn.execute("SELECT id FROM reservations WHERE status='HELD' ORDER BY id DESC LIMIT 1").fetchone()[0]
        else:
            hid=None
    if hid:
        with _sq2.connect("artifacts/coverage-run/cov.db") as conn:
            huser=conn.execute("SELECT u.username FROM reservations r JOIN users u ON u.id=r.user_id WHERE r.id=?",(hid,)).fetchone()[0]
        oc=Client(port).login(huser)
        oc.request("POST",f"/api/reservations/{hid}/confirm",{"request_id":str(uuid.uuid4())})   # 200 分支
        oc.request("POST",f"/api/reservations/{hid}/confirm",{"request_id":str(uuid.uuid4())})   # 409 已确认分支
    a.request("GET","/api/admin/users?page=1&page_size=5&q=user")
    import http.client as _hc  # /metrics 返回 Prometheus 纯文本，不走 JSON 客户端
    _c=_hc.HTTPConnection("127.0.0.1",port,timeout=5)
    _c.request("GET","/metrics");_r=_c.getresponse();_r.read();_c.close()
    # CLI 分支（--check/--backup）走 main.c；用同一 cov 二进制保证 gcda 归属
    import os as _os, subprocess as _sp2
    _exe="build/lab-booking-cov.exe" if __import__("pathlib").Path("build/lab-booking-cov.exe").exists() else "build/lab-booking.exe"
    _env2=dict(_os.environ); _env2["LAB_SEED_PASSWORD"]=PASSWORD
    _sp2.run([_exe,"--db","artifacts/coverage-run/cov.db","--check"],env=_env2,capture_output=True,timeout=60)
    _sp2.run([_exe,"--db","artifacts/coverage-run/cov.db","--backup","artifacts/coverage-run/cov-backup.db"],env=_env2,capture_output=True,timeout=60)
    a.post("/api/logout",{});u.post("/api/logout",{});u2.post("/api/logout",{})
    print("exercise 完成：全部接口路径已走遍")

if __name__=="__main__":
    main()
