"""长时间浸泡实验：混合负载下采样进程工作集与句柄数，断言无泄漏、无错误累积。"""
from __future__ import annotations
import argparse, contextlib, ctypes, datetime, http.client, json, os, pathlib, subprocess, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

class PMC(ctypes.Structure):
    _fields_=[("cb",ctypes.c_ulong),("PageFaultCount",ctypes.c_ulong),
              ("PeakWorkingSetSize",ctypes.c_size_t),("WorkingSetSize",ctypes.c_size_t),
              ("QuotaPeakPagedPoolUsage",ctypes.c_size_t),("QuotaPagedPoolUsage",ctypes.c_size_t),
              ("QuotaPeakNonPagedPoolUsage",ctypes.c_size_t),("QuotaNonPagedPoolUsage",ctypes.c_size_t),
              ("PagefileUsage",ctypes.c_size_t),("PeakPagefileUsage",ctypes.c_size_t)]

def sample(pid):
    k=ctypes.windll.kernel32
    h=k.OpenProcess(0x0400,False,pid)
    if not h:return None
    pmc=PMC();pmc.cb=ctypes.sizeof(pmc)
    ok=ctypes.windll.psapi.GetProcessMemoryInfo(h,ctypes.byref(pmc),pmc.cb)
    handles=ctypes.c_ulong(0);k.GetProcessHandleCount(h,ctypes.byref(handles))
    k.CloseHandle(h)
    return {"ws_mb":round(pmc.WorkingSetSize/1048576,2),"peak_ws_mb":round(pmc.PeakWorkingSetSize/1048576,2),"handles":handles.value} if ok else None

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe",type=pathlib.Path,default=ROOT/"build/lab-booking.exe")
    ap.add_argument("--seconds",type=int,default=600)
    ap.add_argument("--sample-interval",type=int,default=10)
    ap.add_argument("--output",type=pathlib.Path,default=ROOT/"docs/evidence/soak")
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    require(len(PASSWORD)>=8,"LAB_TEST_PASSWORD 未设置")
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    samples=[];counts={"requests":0,"5xx":0,"errors":0}
    lock=threading.Lock()
    run_dir=pathlib.Path(args.output)/"run";run_dir.mkdir(parents=True,exist_ok=True)
    for old in run_dir.glob("baseline.db*"):old.unlink()
    baseline=run_dir/"baseline.db"
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    r=subprocess.run([str(args.exe),"--db",str(baseline),"--seed","--init-only"],env=env,capture_output=True,text=True,timeout=30)
    require(r.returncode==0,f"seed failed: {r.stderr}")
    server=Server(args.exe,run_dir/"srv",baseline,extra=["--rate-burst","100000"]);server.start()
    try:
        writers=[Client(server.port).login(f"user{n:02d}") for n in (11,12)]
        readers=[Client(server.port).login(f"user{n:02d}") for n in (13,14)]
        stop=threading.Event();slots=server.slots()
        def post_retry(client,path,body):
            for _ in range(3): # 契约语义：503 沿用原编号重试
                try:
                    return client.post(path,body)
                except AssertionError as e:
                    if "503" not in str(e):raise
                    time.sleep(.3)
            raise AssertionError("503 重试耗尽: "+path)
        def writer(i):
            slot=slots[i%len(slots)]
            while not stop.is_set():
                try:
                    with lock:counts["requests"]+=1
                    w=post_retry(writers[i],"/api/reservations",{"slot_id":slot,"request_id":uid()})
                    rid=w["data"]["reservation_id"]
                    with lock:counts["requests"]+=1
                    post_retry(writers[i],f"/api/reservations/{rid}/cancel",{"request_id":uid()})
                except AssertionError as e:
                    with lock:counts["errors"]+=1
                    print(f"[writer{i}] {e}",flush=True);time.sleep(.2)
        def reader(i):
            paths=("/api/labs","/api/me/records?page=1&page_size=10","/api/health","/api/slots?lab_id=1&date=2026-09-20")
            while not stop.is_set():
                for p in paths:
                    if stop.is_set():break
                    try:
                        status,_=readers[i%2].request("GET",p)
                        with lock:
                            counts["requests"]+=1
                            if status>=500:counts["5xx"]+=1
                    except AssertionError as e:
                        with lock:counts["errors"]+=1
                        print(f"[reader{i}] {e}",flush=True)
        threads=[threading.Thread(target=writer,args=(i,),daemon=True) for i in range(2)]+[threading.Thread(target=reader,args=(i,),daemon=True) for i in range(2)]
        for t in threads:t.start()
        end=time.monotonic()+args.seconds
        while time.monotonic()<end:
            s=sample(server.process.pid)
            if s:
                s["t"]=round(args.seconds-(end-time.monotonic()),1)
                with lock:s["requests"]=counts["requests"];s["5xx"]=counts["5xx"]
                samples.append(s)
            time.sleep(args.sample_interval)
        stop.set()
        for t in threads:t.join(timeout=5)
        require(server.process.poll() is None,"服务进程在浸泡期间退出")
    finally:
        server.stop()
    require(len(samples)>=3,"采样不足")
    ws0=samples[0]["ws_mb"];ws1=samples[-1]["ws_mb"]
    hs0=samples[0]["handles"];hs1=samples[-1]["handles"]
    with lock:err=counts["errors"];total=counts["requests"];five=counts["5xx"]
    require(err==0,f"请求异常 {err} 次")
    require(five<=total*0.001,f"5xx 占比过高: {five}/{total}")
    require(ws1-ws0<100,f"工作集增长 {ws1-ws0:.1f}MB 超限")
    require(hs1-hs0<500,f"句柄增长 {hs1-hs0} 超限")
    summary={"started":started,"seconds":args.seconds,"executable":str(args.exe.resolve()),"samples":samples,
             "requests":total,"errors":err,"5xx":five,"working_set":{"first_mb":ws0,"last_mb":ws1,"growth_mb":round(ws1-ws0,2)},
             "handles":{"first":hs0,"last":hs1,"growth":hs1-hs0},"passed":True}
    (args.output/"soak.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k!="samples"},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
