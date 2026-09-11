#!/usr/bin/env python3
"""第三方组件完整性校验（供应链测试）。

docs/dependencies.lock.json 锁定 5 个第三方组件的来源与上游档案 SHA256。
本脚本做三层校验：
  V1 锁文件结构：每项含 name/version/url/archive_sha256（64 位十六进制）
  V2 本地树完整性：vendor/ 下逐文件 SHA256 与已备案清单
     （tests/vendor_manifest.json）一致——任何被篡改/误改/遗漏的 vendored
     文件都会被检出（离线可跑，进 CI）
  V3 上游档案校验（可选 --online）：重新下载锁定的档案并核对 archive_sha256

用法：
  python tests/vendor_verify.py            # V1+V2（默认，离线）
  python tests/vendor_verify.py --update   # 首次生成/受控更新本地清单
  python tests/vendor_verify.py --online   # 追加 V3（需网络）
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "docs" / "dependencies.lock.json"
MANIFEST = ROOT / "tests" / "vendor_manifest.json"
TREES = {"civetweb": "vendor/civetweb", "cjson": "vendor/cjson", "sqlite": "vendor/sqlite",
         "unity": "vendor/unity", "sodium": "vendor/sodium"}

def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def scan_tree(rel: str) -> dict:
    base = ROOT / rel
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out[str(p.relative_to(ROOT)).replace("\\", "/")] = sha256(p)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="生成/更新本地文件清单")
    ap.add_argument("--online", action="store_true", help="下载上游档案核对 archive_sha256")
    a = ap.parse_args()

    # V1 锁文件结构
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    require = lambda cond, msg: (_ for _ in ()).throw(SystemExit(f"[FAIL] {msg}")) if not cond else None
    require(isinstance(lock, list) and len(lock) == len(TREES), f"lock entries: {len(lock)}")
    for item in lock:
        require(set(TREES) >= {item["name"]}, f"unknown component {item['name']}")
        require(item["name"] in TREES, f"component without tree: {item['name']}")
        sha = item.get("archive_sha256", "")
        require(len(sha) == 64 and all(c in "0123456789abcdef" for c in sha),
                f"{item['name']}: archive_sha256 malformed")
        require(item.get("url", "").startswith("https://"), f"{item['name']}: url not https")
    print(f"V1 lock structure ok ({len(lock)} components)")

    # V2 本地树与清单比对
    current = {}
    for name, rel in TREES.items():
        require((ROOT / rel).is_dir(), f"missing vendored tree: {rel}")
        current.update(scan_tree(rel))
    require(len(current) > 100, f"suspiciously small vendor tree: {len(current)} files")
    if a.update:
        MANIFEST.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
        print(f"V2 manifest written: {len(current)} files -> {MANIFEST.name}")
        return 0
    require(MANIFEST.exists(), "manifest missing; run --update once to baseline")
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    changed = sorted(k for k in set(current) & set(recorded) if current[k] != recorded[k])
    require(not (added or removed or changed),
            f"vendor drift! +{len(added)} -{len(removed)} ~{len(changed)}: "
            f"{(added + removed + changed)[:5]}")
    print(f"V2 vendor trees match manifest ({len(recorded)} files)")

    # V3 可选上游档案校验（仅允许白名单主机的 https 下载，防 SSRF）
    ALLOWED_HOSTS = {"codeload.github.com", "www.sqlite.org", "github.com"}
    if a.online:
        from urllib.parse import urlsplit
        for item in lock:
            url, want = item["url"], item["archive_sha256"]
            parts = urlsplit(url)
            require(parts.scheme == "https", f"{item['name']}: non-https url")
            require(parts.hostname in ALLOWED_HOSTS,
                    f"{item['name']}: host {parts.hostname} not in allowlist")
            tmp = ROOT / "build" / f"vendor-{item['name']}.archive"
            tmp.parent.mkdir(exist_ok=True)
            with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            got = sha256(tmp)
            require(got == want, f"{item['name']}: upstream archive mismatch {got[:12]}..")
            tmp.unlink()
            print(f"V3 {item['name']}: upstream archive sha256 ok")
    else:
        print("V3 skipped (offline; pass --online to verify upstream archives)")

    print("[PASS] vendor integrity: lock schema + local trees verified")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
