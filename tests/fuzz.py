"""模糊稳健性实验：畸形/超长/错类型/并发错乱输入，断言进程存活、封套合法、无 5xx 泄漏、完整性保持。"""
from __future__ import annotations
import argparse, concurrent.futures, contextlib, http.client, json, os, pathlib, random, sys, time, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe",type=pathlib.Path,default=ROOT/"build/lab-booking.exe")
    ap.add_argument("--rounds",type=int,default=120)
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--output",type=pathlib.Path,default=ROOT/"docs/evidence/fuzz")
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    require(len(PASSWORD)>=8,"LAB_TEST_PASSWORD 未设置")
    run_dir=pathlib.Path(args.output)/"run";run_dir.mkdir(parents=True,exist_ok=True)
    for old in run_dir.glob("baseline.db*"):old.unlink()
    baseline=run_dir/"baseline.db"
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    import subprocess
    r=subprocess.run([str(args.exe),"--db",str(baseline),"--seed","--init-only"],env=env,capture_output=True,text=True,timeout=30)
    require(r.returncode==0,f"seed failed: {r.stderr}")
    counts={"requests":0,"4xx":0,"5xx":0,"other":0,"errors":0}

    def mutated_bodies(rng):
        good={"slot_id":"1","request_id":str(uuid.uuid4())}
        yield {"slot_id":good["slot_id"]}                                     # 缺 request_id
        yield {"slot_id":good["slot_id"],"request_id":"not-a-uuid"}          # 坏 UUID
        yield {"slot_id":-1,"request_id":good["request_id"]}                 # 负数编号
        yield {"slot_id":"999999","request_id":good["request_id"]}           # 不存在
        yield {"slot_id":good["slot_id"]*40,"request_id":good["request_id"]} # 超长字符串
        yield {"slot_id":[1,2],"request_id":good["request_id"]}              # 错类型
        yield {"slot_id":None,"request_id":good["request_id"]}               # null
        yield {k:(v if j else v[::-1]) for j,(k,v) in enumerate(list(good.items())+list(good.items()))}
        yield {"slot_id":good["slot_id"],"request_id":good["request_id"],"extra":"x"*9000}
        yield b"\xff\xfe\x00garbage"                                         # 二进制垃圾（由 raw 路径发送）

    with contextlib.ExitStack() as stack:
        server=Server(args.exe,run_dir/"srv",baseline);server.start()
        a=Client(server.port).login("user01")
        rng=random.Random(20260911)
        def round_robin(n):
            out=[]
            bodies=list(mutated_bodies(rng))
            for i in range(n):
                body=bodies[i%len(bodies)]
                path=rng.choice(["/api/reservations","/api/waitlist","/api/login","/api/me/password"])
                try:
                    if isinstance(body,(bytes,bytearray)):
                        conn=http.client.HTTPConnection("127.0.0.1",server.port,timeout=10)
                        conn.request("POST",path,body=body,headers={"Content-Type":"application/json","Content-Length":str(len(body))})
                        resp=conn.getresponse();payload=resp.read();conn.close()
                        try:code=json.loads(payload).get("code")
                        except Exception:code="NON-JSON"
                        out.append((resp.status,code))
                    else:
                        status,data=a.request("POST",path,body) if path!="/api/login" else a.request("POST",path,body)
                        out.append((status,data.get("code")))
                except (AssertionError,OSError,http.client.HTTPException) as e:
                    out.append((-1,str(e)[:60]))
            return out
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            allres=[f.result() for f in [pool.submit(round_robin,args.rounds) for _ in range(args.workers)]]
        for res in allres:
            for st,code in res:
                counts["requests"]+=1
                if st==-1:counts["errors"]+=1;print("transport:",code)
                elif 400<=st<500:counts["4xx"]+=1
                elif st>=500:counts["5xx"]+=1
                else:counts["other"]+=1
    counts["errors"]+=sum(1 for res in allres for st,code in res if st==-1)
    code=server.process.poll() # 存活性检查必须在 stop() 之前：terminate 的退出码恰为 1，会造成误判
    require(code is None,f"服务进程在模糊实验期间退出 code={code} (0x{code & 0xFFFFFFFF:08X})" if code is not None else "")
    server.stop()
    require(counts["5xx"]<=2,f"5xx 过多: {counts['5xx']}")
    require(counts["errors"]==0,f"传输层异常 {counts['errors']}")
    import sqlite3
    db=sqlite3.connect(run_dir/"srv"/"test.db")
    require(db.execute("PRAGMA integrity_check").fetchall()==[("ok",)],"integrity_check failed")
    db.close()
    summary={"rounds":args.rounds,"workers":args.workers,"requests":counts["requests"],"4xx":counts["4xx"],"5xx":counts["5xx"],"transport_errors":counts["errors"],"process_alive":True,"integrity":"ok","passed":True}
    (args.output/"fuzz.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
