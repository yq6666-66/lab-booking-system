#!/usr/bin/env python3
"""文档测试：验证 README / docs/CONTRACT.md 与系统实际行为一致。

文档漂移是毕业设计与开源项目最常见的「隐性缺陷」：接口改了文档没改、
版本号不一致、引用的脚本文件不存在。本测试固化三类一致性：
  D1 CONTRACT.md 登记的每个非参数化端点真实存在（响应不是 404）
  D2 文档承诺的默认值与业务码真实成立（page_size 默认 20、上限 200）
  D3 /api/health 版本号与 src/app.h 的 LAB_VERSION 一致
  D4 README 引用的脚本/文档文件在仓库中真实存在
"""
from __future__ import annotations
import os, pathlib, re, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integration import Client, Server, require, uid, PASSWORD, ROOT  # noqa: E402

def main():
    # D3 版本一致性（先于起服务，纯静态）
    app_h = (ROOT / "src" / "app.h").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'#define\s+LAB_VERSION\s+"([^"]+)"', app_h)
    require(m, "LAB_VERSION found in app.h")
    header_version = m.group(1)

    run_dir = ROOT / "tests" / "results" / f"doc-{uid()[:6]}"
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
        anon = Client(server.port)

        # D1 CONTRACT.md 端点存在性
        contract = (ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8", errors="replace")
        endpoints = re.findall(r"-\s+(POST|GET)\s+(/api/[A-Za-z0-9/_{}?.=-]*)", contract)
        require(len(endpoints) >= 20, f"contract endpoints parsed: {len(endpoints)}")
        checked, skipped = 0, 0
        for method, path in endpoints:
            if "{" in path:  # 参数化路由单独用真实编号验证
                skipped += 1
                continue
            st, b = admin.request(method, path) if method == "GET" else anon.request(method, path, {"__probe__": 1})
            require(st != 404, f"documented endpoint missing: {method} {path} -> {st}")
            require(set(b.keys()) == {"code", "message", "data"}, f"{path}: envelope drift")
            checked += 1
        # 参数化路由：用真实编号抽样验证路由已挂载
        labs = admin.request("GET", "/api/labs")[1]["data"]["labs"]
        if labs:
            st, b = admin.request("POST", f"/api/admin/labs/{labs[0]['id']}/update",
                                  {"name": labs[0]["name"], "location": "x", "description": "x", "enabled": True})
            require(st != 404, f"admin/labs/{{id}}/update missing: {st}")
            checked += 1
        print(f"D1 endpoints exist: {checked} verified, {skipped} parametric (sampled)")

        # D2 文档承诺的默认值与业务码
        st, b = admin.request("GET", "/api/me/records")
        require(b["data"]["page"] == 1 and b["data"]["page_size"] == 20,
                f"default paging drift: {b['data']['page']},{b['data']['page_size']}")
        st, b = admin.request("GET", "/api/me/records?page_size=200")
        require(st == 200 and b["data"]["page_size"] == 200, "page_size=200 accepted (doc: 1..200)")
        st, b = admin.request("GET", "/api/me/records?page_size=201")
        require(st == 400, "page_size=201 rejected (doc: 1..200)")
        st, b = anon.request("POST", "/api/register", {"username": "admin", "password": PASSWORD})
        require(b["code"] == "USERNAME_TAKEN", f"USERNAME_TAKEN drift: {b['code']}")
        st, b = anon.request("POST", "/api/login", {"username": "admin", "password": PASSWORD + "-x"})
        require(b["code"] == "UNAUTHORIZED", f"UNAUTHORIZED drift: {b['code']}")
        print("D2 documented defaults & codes hold")

        # D3 /api/health version == LAB_VERSION
        st, b = anon.request("GET", "/api/health")
        require(b["data"].get("version") == header_version,
                f"version drift: health={b['data'].get('version')} app.h={header_version}")
        print(f"D3 version consistent: {header_version}")

        # D4 README 引用的文件真实存在
        readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
        refs = set(re.findall(r"(?:tests|scripts|docs)/[A-Za-z0-9_./-]+\.(?:py|ps1|md|json|c)", readme))
        missing = [x for x in sorted(refs) if not (ROOT / x).exists()]
        require(not missing, f"README references missing files: {missing}")
        print(f"D4 README file refs exist: {len(refs)} checked")

        print(f"[PASS] doc: 4 consistency groups verified ({run_dir.name})")
        return 0
    finally:
        server.stop()

if __name__ == "__main__":
    raise SystemExit(main())
