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
    a.post("/api/logout",{});u.post("/api/logout",{});u2.post("/api/logout",{})
    print("exercise 完成：全部接口路径已走遍")

if __name__=="__main__":
    main()
