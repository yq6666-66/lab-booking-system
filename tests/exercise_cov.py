"""覆盖率伴随运行器：启动 cov 服务器 → 走查全部接口 → CTRL_BREAK 优雅停机刷写 gcda。"""
from __future__ import annotations
import argparse, os, pathlib, signal, subprocess, sys, time, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, require, uid, PASSWORD, ROOT  # noqa: E402

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe",type=pathlib.Path,default=ROOT/"build/lab-booking-cov.exe")
    ap.add_argument("--port",type=int,default=8799)
    args=ap.parse_args()
    run_dir=pathlib.Path("artifacts/coverage-run");run_dir.mkdir(parents=True,exist_ok=True)
    for old in run_dir.glob("cov.db*"):old.unlink()
    db=run_dir/"cov.db"
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    r=subprocess.run([str(args.exe),"--db",str(db),"--seed","--init-only"],env=env,capture_output=True,text=True,timeout=60)
    require(r.returncode==0,f"seed failed: {r.stderr}")
    flags=subprocess.CREATE_NEW_PROCESS_GROUP
    proc=subprocess.Popen([str(args.exe),"--db",str(db),"--web",str(ROOT/"web"),"--port",str(args.port),"--rate-burst","100000"],
                          env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    try:
        deadline=time.time()+15
        while time.time()<deadline:
            try:
                ok=urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/health",timeout=2).status==200
                if ok:break
            except Exception:time.sleep(.2)
        require(ok,"cov 服务未就绪")
        sys.argv=[sys.argv[0],str(args.port)]
        import exercise
        exercise.main()
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait(timeout=15)
    require(proc.returncode==0,f"优雅停机退出码异常: {proc.returncode}")
    print("cov 运行器完成：gcda 已刷写")

if __name__=="__main__":
    main()
