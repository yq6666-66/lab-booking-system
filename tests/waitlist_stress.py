"""候补并发压力实验：多用户并发竞争有限席位，验证 FIFO 公平性与无重复。"""
from __future__ import annotations
import argparse, concurrent.futures, contextlib, datetime, json, os, pathlib, subprocess, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe",type=pathlib.Path,default=ROOT/"build/lab-booking.exe")
    ap.add_argument("--users",type=int,default=20)
    ap.add_argument("--labs",type=int,default=5)
    ap.add_argument("--output",type=pathlib.Path,default=ROOT/"docs/evidence/waitlist-stress")
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    require(len(PASSWORD)>=8,"LAB_TEST_PASSWORD 未设置")
    run_dir=args.output/"run";run_dir.mkdir(parents=True,exist_ok=True)
    for old in run_dir.glob("stress.db*"):old.unlink()
    baseline=run_dir/"stress.db"
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    r=subprocess.run([str(args.exe),"--db",str(baseline),"--seed","--init-only"],env=env,capture_output=True,text=True,timeout=30)
    require(r.returncode==0,f"seed failed: {r.stderr}")

    server=Server(args.exe,run_dir/"srv",baseline,extra=["--rate-burst","100000"]);server.start()
    try:
        admin=Client(server.port).login("admin")
        import datetime as _dt
        day=(datetime.datetime.now(datetime.timezone.utc)+_dt.timedelta(hours=48)).strftime("%Y-%m-%d")
        # 创建 labs 个实验室，各发布 1 个场次
        lab_ids=[]
        for i in range(args.labs):
            lab=admin.post("/api/admin/labs",{"name":f"压测实验室{i}","location":"实验楼","description":"stress"})["data"]["lab_id"]
            admin.post("/api/admin/slots/publish",{"lab_id":lab,"start_date":day,"end_date":day})
            sl=Client(server.port).login("user01").request("GET","/api/slots?lab_id="+lab+"&date="+day)[1]["data"]["slots"]
            lab_ids.append((lab,sl[0]["id"]))
        # 每个场次容量 1，但发布时未设 capacity，需手动调低
        for lab_id,sid in lab_ids:
            server.sql("UPDATE slots SET capacity=1 WHERE id=?",(sid,))

        # N 个用户并发竞争：每场次约 users/labs 个用户尝试预约+候补
        users_per_lab=max(args.users//args.labs,2)
        all_clients=[Client(server.port).login(f"user{n:02d}") for n in range(1,args.users+1)]
        results={"reserved":0,"waitlisted":0,"wait_full":0,"errors":0,"promoted":0,"fifo_ok":True}
        lock=threading.Lock()

        def compete(worker_id):
            lab_id,sid=lab_ids[worker_id%args.labs]
            cl=all_clients[worker_id%len(all_clients)]
            st,body=cl.request("POST","/api/reservations",{"slot_id":sid,"request_id":uid()})
            with lock:
                if st==200:results["reserved"]+=1
                elif st==409 and body.get("code")=="SLOT_FULL":results["wait_full"]+=1
                else:results["errors"]+=1
            # 满员后尝试候补
            if st==409:
                st2,body2=cl.request("POST","/api/waitlist",{"slot_id":sid,"request_id":uid()})
                with lock:
                    if st2==200:results["waitlisted"]+=1
                    else:results["errors"]+=1

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.users) as pool:
            futs=[pool.submit(compete,i) for i in range(args.users)]
            for f in futs:f.result()

        # 验证每个场次恰有 1 条 CONFIRMED
        for lab_id,sid in lab_ids:
            cnt=server.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(sid,))[0][0]
            require(cnt==1,f"slot {sid}: confirmed={cnt}, expected 1")
        require(results["errors"]==0,f"errors: {results['errors']}")
        require(results["reserved"]==args.labs,f"reserved {results['reserved']} != {args.labs} labs")

        # 释放全部席位 → FIFO 补位验证
        confirmed=server.sql("SELECT id,slot_id,user_id FROM reservations WHERE status='CONFIRMED' ORDER BY slot_id,id")
        for rid,sid,_ in confirmed:
            owner=server.sql("SELECT username FROM users WHERE id=?",(rid and server.sql("SELECT user_id FROM reservations WHERE id=?",(rid,))[0][0],))
            # 找到预约者并用同一用户取消
            un=server.sql("SELECT username FROM users WHERE id=(SELECT user_id FROM reservations WHERE id=?)",(rid,))[0][0]
            cl=Client(server.port).login(un)
            cl.request("POST",f"/api/reservations/{rid}/cancel",{"request_id":uid()})
        # 验证每个场次仍然恰有 1 条 CONFIRMED（候补补位）
        for lab_id,sid in lab_ids:
            cnt=server.sql("SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'",(sid,))[0][0]
            require(cnt==1,f"slot {sid} after cancel: confirmed={cnt}")
        # FIFO：每个场次的 PROMOTED 候补编号 < 后续 WAITING 编号
        fifo_violations=server.sql(
            "SELECT count(*) FROM waitlist w1 WHERE w1.status='PROMOTED' AND EXISTS("
            " SELECT 1 FROM waitlist w2 WHERE w2.slot_id=w1.slot_id AND w2.status='WAITING' AND w2.id<w1.id)")
        require(fifo_violations[0][0]==0,f"FIFO violations: {fifo_violations[0][0]}")
        results["promoted"]=server.sql("SELECT count(*) FROM waitlist WHERE status='PROMOTED'")[0][0]
        results["fifo_ok"]=True
        server.stop()
    except Exception:
        server.stop();raise

    summary={"users":args.users,"labs":args.labs,**results,"passed":True}
    (args.output/"waitlist_stress.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k!="samples"},ensure_ascii=False))
    return 0

if __name__=="__main__":
    import threading
    raise SystemExit(main())
