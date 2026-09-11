"""文档一致性测试：核对「契约声明 ↔ 代码实现 ↔ 仓库真实文件」三者是否自洽（仅 Python 标准库）。

三项核查：
1. docs/CONTRACT.md 声明的每个 REST 端点，能在 src/http.c 的 dispatch 中找到对应路由字面片段；
2. CONTRACT.md 列出的错误码字符串，确实在服务端源码中被使用；
3. README.md 引用的每个本地文件真实存在，且 docs/dependencies.lock.json 的每个组件在 vendor/ 下有目录。
结果写入 docs/evidence/doc/doc_results.json，退出码 0 表示全部通过。
"""
from __future__ import annotations
import argparse, datetime, json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = []

ENDPOINT_RE = re.compile(r"\b(GET|POST)\s+(/api/[A-Za-z0-9/_{}?=&.,-]+)")
ERROR_CODE_RE = re.compile(r"\b([45]\d\d)\s+((?:[A-Z_]{3,})(?:/[A-Z_]{3,})*)")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

def record(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "passed": True, "detail": detail})
        print(f"  PASS  {name}" + (f"  ->  {detail}" if detail else ""), flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

def read(path):
    target = ROOT / path
    if not target.is_file():
        raise AssertionError(f"文件不存在：{path}")
    return target.read_text(encoding="utf-8", errors="replace")

def contract_endpoints():
    """提取 CONTRACT.md 中声明的 (method, path) 列表。"""
    text = read("docs/CONTRACT.md")
    seen, out = set(), []
    for method, path in ENDPOINT_RE.findall(text):
        if method == "POST" and path == "/api/login" and "/api/logout" in path: pass
        key = (method, path.split("?")[0])
        if key in seen: continue
        seen.add(key); out.append(key)
    return out

def literal_fragments(path):
    """把 /api/reservations/{id}/cancel 拆成可在源码中检索的字面片段。"""
    parts = re.split(r"\{[^}]*\}", path)
    return [p for p in parts if len(p) > 1]

def register_checks():
    contract = contract_endpoints()
    server_src = read("src/http.c")
    docs_src = read("docs/CONTRACT.md")
    def check_all_routes():
        missing = []
        for method, path in contract:
            for frag in literal_fragments(path):
                if f'"{frag}"' not in server_src:
                    missing.append(f"{method} {path} -> 源码缺少字面量 \"{frag}\"")
        if missing:
            raise AssertionError("；".join(missing[:8]))
        return f"{len(contract)} 个端点在 src/http.c 中均有对应路由片段"
    record("CONTRACT 声明的端点均在 src/http.c 中有路由", check_all_routes)

    def check_error_codes():
        declared = set()
        for _status, codes in ERROR_CODE_RE.findall(docs_src):
            for c in codes.split("/"): declared.add(c.strip())
        declared = {c for c in declared if not c.isdigit()}
        src_all = server_src + read("src/service.c") + read("src/db.c") + read("src/ratelimit.c")
        absent = sorted(c for c in declared if f'"{c}"' not in src_all)
        if absent:
            raise AssertionError(f"契约声明但源码未使用的错误码：{absent}")
        return f"{len(declared)} 个错误码均在源码中使用：{sorted(declared)}"
    record("CONTRACT 的错误码均在服务端源码中出现", check_error_codes)

    def check_error_status_pairs():
        text = read("docs/CONTRACT.md")
        pairs = [("400", "INVALID_INPUT"), ("401", "UNAUTHORIZED"), ("403", "FORBIDDEN"),
                 ("404", "NOT_FOUND"), ("409", "STATE_CONFLICT"), ("413", "TOO_LARGE"),
                 ("503", "DATABASE_BUSY"), ("500", "INTERNAL_ERROR")]
        absent = [f"{s}/{c}" for s, c in pairs if c not in text]
        if absent: raise AssertionError(f"契约缺少错误码说明：{absent}")
        return f"{len(pairs)} 组状态码与错误码配对均在契约中声明"
    record("契约声明了各状态码对应的错误码", check_error_status_pairs)

def readme_checks():
    def check_readme_links():
        text = read("README.md")
        links, missing = [], []
        for raw in MD_LINK_RE.findall(text):
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")): continue
            links.append(target)
            if not (ROOT / target).exists(): missing.append(target)
        if missing:
            raise AssertionError(f"README 引用但不存在：{sorted(set(missing))}")
        return f"{len(set(links))} 个本地引用全部存在"
    record("README.md 引用的本地文件均存在", check_readme_links)

    def check_docs_exist():
        required = ["docs/CONTRACT.md", "docs/PROGRESS.md", "docs/TEST_REPORT.md",
                    "docs/CHANGELOG.md", "docs/dependencies.lock.json", "LICENSE", "tests/README.md"]
        missing = [p for p in required if not (ROOT / p).exists()]
        if missing: raise AssertionError(f"关键文档缺失：{missing}")
        return f"{len(required)} 个关键文档齐全"
    record("关键文档齐全", check_docs_exist)

def dependency_checks():
    def check_vendor_dirs():
        lock = json.loads(read("docs/dependencies.lock.json"))
        missing, found = [], []
        for comp in lock:
            name = comp.get("name", "?")
            candidates = [ROOT / "vendor" / name]
            hit = next((c for c in candidates if c.is_dir()), None)
            if hit: found.append(name)
            else: missing.append(name)
        if missing:
            raise AssertionError(f"dependencies.lock.json 中的组件在 vendor/ 下无目录：{missing}")
        return f"{len(found)} 个组件目录齐全：{found}"
    record("dependencies.lock.json 的每个组件在 vendor/ 下有目录", check_vendor_dirs)

    def check_lock_fields():
        lock = json.loads(read("docs/dependencies.lock.json"))
        problems = []
        for comp in lock:
            for field in ("name", "version", "url", "archive_sha256"):
                if not comp.get(field): problems.append(f"{comp.get('name','?')} 缺少 {field}")
            sha = comp.get("archive_sha256", "")
            if sha and not re.fullmatch(r"[0-9a-f]{64}", sha): problems.append(f"{comp.get('name','?')} 的 archive_sha256 不是 64 位十六进制")
        if problems: raise AssertionError("；".join(problems))
        return f"{len(lock)} 条依赖记录字段完整且哈希格式正确"
    record("依赖锁字段完整、哈希格式正确", check_lock_fields)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/doc")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print("== 契约 ↔ 代码 ==", flush=True)
    register_checks()
    print("== README ↔ 文件 ==", flush=True)
    readme_checks()
    print("== 依赖锁 ↔ vendor ==", flush=True)
    dependency_checks()
    passed = sum(1 for r in RESULTS if r["passed"])
    report = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks_total": len(RESULTS), "checks_passed": passed,
        "all_passed": passed == len(RESULTS), "results": RESULTS,
    }
    (args.output / "doc_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n文档一致性检查 {passed}/{len(RESULTS)} 通过")
    print(f"证据：{(args.output / 'doc_results.json').resolve()}")
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
