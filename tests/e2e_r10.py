#!/usr/bin/env python3
"""R10 浏览器端到端验收（Playwright sync API）。

运行：python tests/e2e_r10.py [--exe build/lab-booking.exe]
- 自建服务（随机端口），口令经环境变量 LAB_TEST_PASSWORD 传入（至少 8 位，缺省报错）。
- 服务启动复用 integration.py 的 Server（baseline 由 --seed --init-only 生成，LAB_SEED_PASSWORD 同口令）。
- 每个用例独立 try/except 收集结果；失败时对当前页面截图存 tests/results/e2e-r10/。
- 整体超时 300 秒；最后一行输出 JSON 摘要 {"passed": n, "failed": [...], "total": N}，
  全部通过 exit 0，否则 exit 1（环境/预置失败 exit 2）。

已侦察确认的选择器（工作区 web/ 现状）：
- index.html/app.js：#login-tab-user / #login-tab-admin / #login-submit（管理员 Tab 文案变「进入管理台」）、
  #login-register-entry（管理员 Tab 隐藏）、#login-error、#login-form、#app-view、
  #notify-btn / #notify-panel / #notify-list（.notify-item）、#logout、nav.tabs [data-tab]；
  app.js 在管理员登录成功后 location.href='/admin.html'。
- admin.html/admin.js：h1「运行概览」、section.cards 内 5 张 article.card、#tabs [data-tab]、
  #notice（成功/失败提示条）、#app（登录 gate 后显示）。
- 后端 src/http.c：user 账号走管理员入口返回 403 ROLE_MISMATCH「该账号不是管理员，请使用用户入口登录」。

不确定的 R10 UI 假设（工作区尚未包含对应实现，一律采用 id 猜测 → 可见语义选择器多级回退）：
- 管理台「场次管理」「通知发布」Tab 与用户端「签到」Tab 的具体控件 id 未知；
- 「场次管理」的查询/修改/保存控件、「通知发布」的受众与标题/正文控件、「今日签到」空态文案未知。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import traceback

TESTS = pathlib.Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from integration import ROOT, PASSWORD, Client, Server, require, uid  # noqa: E402

try:  # Windows 控制台默认 GBK，统一为 UTF-8 防止中文输出报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import expect, sync_playwright  # noqa: E402

RESULTS_DIR = TESTS / "results" / "e2e-r10"
STARTED = time.monotonic()
OVERALL_SECONDS = 300
BJ = datetime.timezone(datetime.timedelta(hours=8))
NOTIFY_TITLE = "E2E 通知测试"
NOTIFY_BODY = "R10 端到端验收广播：请留意实验室开放时间调整。"


def bj_today() -> str:
    return datetime.datetime.now(BJ).strftime("%Y-%m-%d")


def bj_day_start() -> int:
    now = int(time.time())
    return (now + 28800) // 86400 * 86400 - 28800


def shot(page, tag: str) -> None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(RESULTS_DIR / f"{tag}-{int(time.time() * 1000)}.png"), full_page=True)
    except Exception:
        pass  # 截图失败不影响结果收集


def first_visible(page, selectors, timeout=2000):
    """按候选顺序返回第一个可见控件；全部不可见则抛错（附全部候选便于定位）。

    候选统一追加 :visible 伪类：管理台各 Tab 分区同屏共存（hidden 切换），
    避免 .first 命中隐藏分区里的同名控件（如隐藏统计视图中的「查询」按钮）。
    """
    last = None
    for sel in selectors:
        loc = page.locator(sel if sel.endswith(":visible") else sel + ":visible")
        try:
            loc.first.wait_for(state="visible", timeout=timeout)
            return loc.first
        except Exception as exc:
            last = exc
    raise AssertionError(f"候选选择器均未出现可见控件：{selectors}（最后错误：{last}）")


def attach_dialogs(page) -> None:
    page.on("dialog", lambda d: d.accept())


def fill_login(page, username: str, admin_tab: bool) -> None:
    """在登录页填账号口令并提交；admin_tab=False 表示沿用当前 Tab（用户或已切好的管理员）。"""
    if admin_tab:
        page.click("#login-tab-admin")
    page.fill("#login-form input[name='username']", username)
    page.fill("#login-form input[name='password']", PASSWORD)
    page.click("#login-submit")


def prepare(server):
    """API 预置：专用实验室 + 北京今天场次 + user01 的今日预约（使签到卡片分支更可能命中）。

    publish 会跳过已开始的时刻；若今天因此 0 场次（深夜运行），把该实验室一个未来场次
    挪到今天晚些时候（北京时间 22:50 之前），保证「场次管理」按今天查询有数据行。
    """
    admin = Client(server.port).login("admin")
    lab_name = f"E2E 场次管理实验室{uid()[:6]}"
    lab = str(admin.post("/api/admin/labs", {"name": lab_name, "location": "信息楼", "description": "R10 端到端验收"})["data"]["lab_id"])
    day = bj_today()
    admin.post("/api/admin/slots/publish", {"lab_id": lab, "start_date": day, "end_date": day, "capacity": "3"})
    slots = admin.request("GET", f"/api/slots?lab_id={lab}&date={day}")[1]["data"]["slots"]
    if not slots:
        future = server.sql("SELECT id FROM slots WHERE lab_id=? AND start_at>? ORDER BY start_at LIMIT 1", (lab, int(time.time())))
        require(future, "今天无场次且无未来场次可挪")
        start = min(int(time.time()) + 180, bj_day_start() + 23 * 3600 - 600)
        if start <= int(time.time()):
            start = int(time.time()) + 60  # 已过北京 22:50 的极端情形：允许跨日，报告中注明
        server.sql("UPDATE slots SET start_at=?, end_at=? WHERE id=?", (start, start + 3600, future[0][0]))
        slots = admin.request("GET", f"/api/slots?lab_id={lab}&date={day}")[1]["data"]["slots"]
    require(slots, "预置后今天仍无场次")
    user = Client(server.port).login("user01")
    bookable = next((s for s in slots if int(s["start_at"]) > time.time()), None)
    if bookable:  # 全部已开始时跳过预约（case4 走空态分支）
        user.post("/api/reservations", {"slot_id": bookable["id"], "request_id": uid()})
    return lab, lab_name


def case1(env):
    """管理员双 Tab 登录跳转：切管理员 Tab → 文案/注册入口断言 → 登录 → /admin.html 概览。"""
    page, base = env["admin_page"], env["base"]
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("#login-form").wait_for(state="visible", timeout=10000)
    page.click("#login-tab-admin")
    expect(page.locator("#login-submit")).to_have_text("进入管理台", timeout=5000)
    expect(page.locator("#login-register-entry")).to_be_hidden(timeout=5000)
    expect(page.locator("#login-subtitle")).to_contain_text("管理员账号", timeout=5000)
    fill_login(page, "admin", admin_tab=False)
    page.wait_for_url("**/admin.html", timeout=15000)
    page.locator("#app").wait_for(state="visible", timeout=10000)
    expect(page.locator("h1", has_text="运行概览")).to_be_visible(timeout=5000)
    cards = page.locator(".cards article.card")
    require(cards.count() >= 5, f"概览卡片数量 {cards.count()} < 5")
    return {"cards": cards.count(), "url": page.url}


def case2(env):
    """场次修改流：进 Tab → 实验室+今天 → 查询出表格行 → 第一行修改容量为 5 → 保存。"""
    page = env["admin_page"]
    first_visible(page, ["#tabs button[data-tab='slots']", "#tabs button:has-text('场次管理')"], 3000).click()
    sel = first_visible(page, ["#slot-lab", "#slots-lab", "#query-lab", "select:visible"], 3000)
    sel.select_option(label=env["lab_name"])
    first_visible(page, ["#slot-date", "#slots-date", "input[type='date']:visible"], 3000).fill(bj_today())
    first_visible(page, ["#refresh-admin-slots", "button:has-text('查询场次')", "button:has-text('查询')"], 3000).click()
    rows = page.locator("table:visible tbody tr")
    rows.first.wait_for(state="visible", timeout=8000)
    require(rows.count() >= 1, f"场次表格无数据行（{rows.count()} 行）")
    first_visible(page, ["button:has-text('修改')"], 4000).click()
    first_visible(page, ["input[type='number']:visible", "td input:visible"], 4000).fill("5")
    first_visible(page, ["button:has-text('保存')", "button:has-text('确定')", "button:has-text('提交')"], 4000).click()
    ok = False
    try:
        notice = page.locator("#notice")
        expect(notice).to_be_visible(timeout=5000)
        text = notice.inner_text()
        ok = any(key in text for key in ("保存", "成功", "已更新", "已修改"))
    except Exception:
        pass
    if not ok:  # 未捕捉到成功提示则重新查询，核对表格容量显示 5
        try:
            first_visible(page, ["button:has-text('查询场次')", "button:has-text('查询')"], 2000).click()
        except Exception:
            pass
        page.wait_for_timeout(800)
        cells = page.locator("table:visible td").all_inner_texts()
        require(any(re.search(r"(^|[^0-9])5($|[^0-9])", c.strip()) for c in cells),
                f"未见成功提示且表格中无容量 5：{cells[:12]}")
    return {"capacity": 5}


def case3(env):
    """通知发布流：进 Tab → 全体用户 → 标题/正文 → 发送 → 提示含「已发送给」。"""
    page = env["admin_page"]
    first_visible(page, ["#tabs button[data-tab='notify']", "#tabs button[data-tab='broadcast']", "#tabs button:has-text('通知发布')"], 3000).click()
    audience = False
    try:  # 受众优先：下拉框里的「全体用户」选项
        page.locator("select:visible").first.select_option(label="全体用户")
        audience = True
    except Exception:
        pass
    if not audience:
        try:  # 回退：label 包裹的单选/复选框
            page.locator("label:has-text('全体用户') input").first.check(timeout=2500)
            audience = True
        except Exception:
            pass
    require(audience, "未找到「全体用户」受众控件")
    first_visible(page, ["#notify-title", "input[name='title']", "input[placeholder*='标题']", "input[placeholder*='通知']"], 3000).fill(NOTIFY_TITLE)
    first_visible(page, ["#notify-body", "textarea[name='body']", "textarea[name='content']", "textarea:visible"], 3000).fill(NOTIFY_BODY)
    first_visible(page, ["button:has-text('发送')", "button:has-text('发布通知')", "button:has-text('提交')"], 3000).click()
    expect(page.locator("#notice")).to_contain_text("已发送给", timeout=8000)
    return {"title": NOTIFY_TITLE}


def case4(env):
    """用户端签到 Tab：user01 登录 → 顶部「签到」Tab → 今日签到区域渲染（卡片或空态）→ 通知铃铛。"""
    page, base = env["user_page"], env["base"]
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("#login-form").wait_for(state="visible", timeout=10000)
    fill_login(page, "user01", admin_tab=False)
    page.locator("#app-view").wait_for(state="visible", timeout=10000)
    first_visible(page, ["nav.tabs button[data-tab='checkin']", "nav.tabs button:has-text('签到')"], 3000).click()
    expect(page.get_by_text("今日签到").first).to_be_visible(timeout=5000)
    card = None
    for sel in ("button:has-text('立即签到'):visible", "article.slot:visible", ".checkin-card:visible",
                "#checkin-list article:visible", "#checkin-view article:visible", "[id*='checkin'] article:visible"):
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="visible", timeout=400)
            card = loc
            break
        except Exception:
            continue
    if card is not None:
        require(card.count() >= 1, "今日签到卡片存在性断言失败")
        branch = "card"
    else:
        try:
            first_visible(page, ["#checkin-view .empty", ".empty"], 1500)
        except Exception:
            expect(page.get_by_text(re.compile("暂无|没有|无可签到")).first).to_be_visible(timeout=3000)
        branch = "empty"
    page.click("#notify-btn")
    page.locator("#notify-panel").wait_for(state="visible", timeout=5000)
    item = page.locator("#notify-list .notify-item", has_text=NOTIFY_TITLE)
    try:
        item.first.wait_for(state="visible", timeout=6000)
    except Exception:  # 通知条目样式变化时回退为面板文本包含标题
        expect(page.locator("#notify-list").first).to_contain_text(NOTIFY_TITLE, timeout=4000)
    return {"checkin_render": branch, "notification": NOTIFY_TITLE}


def case5(env):
    """普通用户管理员入口 403：登出 → 切管理员 Tab → user01 登录 → 错误提示「该账号不是管理员」。"""
    page = env["user_page"]
    page.click("#logout")
    page.locator("#login-form").wait_for(state="visible", timeout=6000)
    page.click("#login-tab-admin")
    expect(page.locator("#login-submit")).to_have_text("进入管理台", timeout=4000)
    fill_login(page, "user01", admin_tab=False)
    expect(page.locator("#login-error")).to_contain_text("该账号不是管理员", timeout=8000)
    require("/admin.html" not in page.url, f"普通用户不应进入管理台：{page.url}")
    return {"error": "该账号不是管理员"}


def record_case(name: str, fn, page_for_shot):
    row = {"test": name, "passed": False}
    try:
        details = fn()
        row["passed"] = True
        if details:
            row["details"] = details
    except Exception as exc:
        if page_for_shot is not None:
            shot(page_for_shot, re.sub(r"\W+", "-", name))
        row["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def run(pw, exe) -> int:
    runs_dir = ROOT / "artifacts" / "test-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = pathlib.Path(tempfile.mkdtemp(prefix="e2e-r10-", dir=runs_dir))
    baseline = run_path / "baseline.db"
    seed_env = os.environ.copy()
    seed_env["LAB_SEED_PASSWORD"] = PASSWORD
    seed = subprocess.run([str(exe), "--db", str(baseline), "--seed", "--init-only"],
                          env=seed_env, capture_output=True, text=True, timeout=60)
    require(seed.returncode == 0, f"seed failed: {seed.returncode}: {seed.stdout} {seed.stderr}")
    server = Server(exe, run_path / "web-e2e", baseline)
    print(f"E2E run directory: {run_path}", file=sys.stderr, flush=True)
    try:
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        lab, lab_name = prepare(server)
        browser = pw.chromium.launch(headless=True)
        try:
            ctx_admin = browser.new_context(locale="zh-CN")
            ctx_user = browser.new_context(locale="zh-CN")
            admin_page, user_page = ctx_admin.new_page(), ctx_user.new_page()
            for page in (admin_page, user_page):
                page.set_default_timeout(8000)
                page.set_default_navigation_timeout(15000)
                attach_dialogs(page)
            env = {"base": base, "admin_page": admin_page, "user_page": user_page, "lab": lab, "lab_name": lab_name}
            cases = [
                ("case1 管理员双Tab登录跳转", lambda: case1(env), admin_page),
                ("case2 场次修改流", lambda: case2(env), admin_page),
                ("case3 通知发布流", lambda: case3(env), admin_page),
                ("case4 用户端签到Tab与通知", lambda: case4(env), user_page),
                ("case5 普通用户管理员入口403", lambda: case5(env), user_page),
            ]
            rows = []
            for name, fn, page_for_shot in cases:
                if time.monotonic() - STARTED > OVERALL_SECONDS:
                    row = {"test": name, "passed": False, "error": "整体 300 秒超时，用例未执行"}
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                    continue
                rows.append(record_case(name, fn, page_for_shot))
        finally:
            try:
                browser.close()
            except Exception:
                pass
    finally:
        server.stop()
    passed = sum(1 for r in rows if r["passed"])
    failed = [r["test"] for r in rows if not r["passed"]]
    print(json.dumps({"passed": passed, "failed": failed, "total": len(rows)}, ensure_ascii=False), flush=True)
    return 0 if not failed else 1


def main():
    parser = argparse.ArgumentParser(description="R10 浏览器端到端验收（Playwright）")
    parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "build" / "lab-booking.exe",
                        help="服务端可执行文件（默认 build/lab-booking.exe）")
    args = parser.parse_args()
    if len(PASSWORD) < 8:
        print("缺少环境变量 LAB_TEST_PASSWORD（需至少 8 位测试专用口令）", file=sys.stderr)
        return 2
    if not args.exe.is_file():
        print(f"缺少服务端可执行文件：{args.exe}", file=sys.stderr)
        return 2
    with sync_playwright() as pw:
        return run(pw, args.exe)


if __name__ == "__main__":
    raise SystemExit(main())
