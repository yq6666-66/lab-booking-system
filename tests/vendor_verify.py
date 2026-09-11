"""第三方组件完整性校验：核对 vendor/ 是否齐备，并对关键源码做 SHA256 基线比对（仅 Python 标准库）。

关于两层哈希的说明（重要）：
  docs/dependencies.lock.json 记录的是**下载归档**（zip / tar.gz）的 archive_sha256，
  而 vendor/ 下保存的是**解压后的内容** —— 两者不是同一对象，无法直接比较数值。
因此本脚本把完整性拆成两层：
  第 1 层 存在性：lock 中每个组件在 vendor/ 下有目录，且能找到关键源码/头文件；
  第 2 层 一致性：对关键文件计算 SHA256，与基线文件（docs/evidence/vendor/vendor_baseline.json）比对。
                首次运行写入基线（记为 BASELINE），其后每次运行据此检测 vendor 被改动或损坏（MATCH/MISMATCH）。
同时把 lock 声明的 archive_sha256 原样列出，便于与 fetch_dependencies.py 的下载校验对照。
结果写入 docs/evidence/vendor/vendor_results.json，退出码 0 表示全部通过。
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = []

# 每个组件用于完整性校验的关键文件（按文件名在 vendor/<name>/ 下递归查找）
KEY_FILES = {
    "civetweb": ["civetweb.c", "civetweb.h"],
    "cjson": ["cJSON.c", "cJSON.h"],
    "sqlite": ["sqlite3.c", "sqlite3.h"],
    "unity": ["unity.c", "unity.h"],
    "sodium": ["sodium.h"],
}

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def read(path):
    target = ROOT / path
    require(target.is_file(), f"文件不存在：{path}")
    return target.read_text(encoding="utf-8", errors="replace")

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def record(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "passed": True, "detail": detail})
        print(f"  PASS  {name}" + (f"  ->  {detail}" if detail else ""), flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

def locate(name, filename):
    root = ROOT / "vendor" / name
    hits = sorted(p for p in root.rglob(filename) if p.is_file())
    return hits[0] if hits else None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/vendor")
    parser.add_argument("--update-baseline", action="store_true", help="强制重写基线（默认仅首次生成）")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline_path = args.output / "vendor_baseline.json"
    lock = json.loads(read("docs/dependencies.lock.json"))
    components = [c["name"] for c in lock]
    hashes, missing, archive_info = {}, [], []

    def check_dirs():
        absent = [c for c in components if not (ROOT / "vendor" / c).is_dir()]
        require(not absent, f"vendor/ 下缺少组件目录：{absent}")
        return f"{len(components)} 个组件目录齐全：{components}"
    record("各组件在 vendor/ 下有目录", check_dirs)

    def check_key_files():
        problems = []
        for name in components:
            for filename in KEY_FILES.get(name, []):
                hit = locate(name, filename)
                if hit is None:
                    problems.append(f"{name}/{filename}")
                    continue
                hashes[f"{name}/{filename}"] = {"path": str(hit.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256_of(hit)}
        require(not problems, f"缺少关键文件：{problems}")
        return f"定位并哈希 {len(hashes)} 个关键文件"
    record("关键源码/头文件齐备", check_key_files)

    def check_against_baseline():
        require(hashes, "未采集到任何哈希，无法比对")
        if args.update_baseline or not baseline_path.is_file():
            baseline_path.write_text(json.dumps({"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                                 "note": "vendor 关键文件 SHA256 基线（解压后内容）；lock 中的 archive_sha256 对应下载归档，两者不同层",
                                                 "files": hashes}, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"首次生成基线（{len(hashes)} 项），写入 {baseline_path.relative_to(ROOT)}"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["files"]
        mismatched, gone = [], []
        for key, meta in baseline.items():
            if key not in hashes: gone.append(key); continue
            if hashes[key]["sha256"] != meta["sha256"]: mismatched.append(key)
        require(not mismatched, f"与基线不一致（可能被篡改）：{mismatched}")
        require(not gone, f"基线中记录但当前缺失：{gone}")
        return f"{len(baseline)} 项与基线逐一 MATCH"
    record("关键文件 SHA256 与基线一致", check_against_baseline)

    def check_lock_archive_hashes():
        rows = []
        for comp in lock:
            sha = comp.get("archive_sha256", "")
            require(re.fullmatch(r"[0-9a-f]{64}", sha or ""), f"{comp['name']} 的 archive_sha256 格式不合法：{sha!r}")
            rows.append(f"{comp['name']}@{comp['version']}")
        archive_info.extend(rows)
        return f"{len(rows)} 条归档哈希格式合法：{rows}"
    record("依赖锁归档哈希格式合法", check_lock_archive_hashes)

    passed = sum(1 for r in RESULTS if r["passed"])
    report = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks_total": len(RESULTS), "checks_passed": passed, "all_passed": passed == len(RESULTS),
        "components": components, "archive_hashes_from_lock": archive_info,
        "file_hashes": hashes,
        "baseline_file": str(baseline_path.relative_to(ROOT)).replace("\\", "/"),
        "results": RESULTS,
    }
    (args.output / "vendor_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n第三方组件校验 {passed}/{len(RESULTS)} 通过")
    print(f"证据：{(args.output / 'vendor_results.json').resolve()}")
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
