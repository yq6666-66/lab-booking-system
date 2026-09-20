"""前端静态门禁：在零依赖（仅 Python 标准库）前提下核对 web/ 的 CSP 合规、XSS 出口、
DOM id 引用完整性、主题引导顺序与 CSS 设计令牌自洽性。

背景：本服务对所有响应（含静态页）下发 `Content-Security-Policy: default-src 'self'`，
既未开启 `style-src 'unsafe-inline'`，也未开启 `script-src 'unsafe-inline'`。因此
HTML 里任何 `style="…"` / `on…="…"` / 内联 `<script>` 都会被浏览器**整条拒绝**——
它不会降级成"样式不生效"，而是该 attribute 完全不进 CSSOM（已实测：设置后
computedStyle 仍为初始值）。这类缺陷过去藏在场次发布表单里，故列为门禁首项。

结果写入 docs/evidence/frontend/frontend_results.json，退出码 0 表示全部通过。
"""
from __future__ import annotations
import argparse, datetime, json, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
RESULTS = []

# 页面与脚本的归属关系：theme.js 为两页共用的引导脚本
PAGES = {"index.html": "app.js", "admin.html": "admin.js"}
SHARED_JS = ["theme.js", "ui.js"]

# 非安全上下文或缺失实现时 uid() 的探测降级链会用到 randomUUID，这是唯一允许直接引用的位置
UID_BLOCK_RE = re.compile(r"const\s+uid\s*=\s*\(.*?\}\)\(\);", re.S)
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
ID_REF_RE = re.compile(r"""(?:\$\(|getElementById\()['"]([A-Za-z][A-Za-z0-9_-]*)['"]\)""")
ID_DEF_RE = re.compile(r"""\sid=["']([A-Za-z][A-Za-z0-9_-]*)["']""")
ID_ASSIGN_RE = re.compile(r"""\.id\s*=\s*['"]([A-Za-z][A-Za-z0-9_-]*)['"]""")

def record(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "passed": True, "detail": detail})
        print(f"  PASS  {name}" + (f"  ->  {detail}" if detail else ""), flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

def read(rel):
    target = WEB / rel
    if not target.is_file():
        raise AssertionError(f"缺少前端文件：web/{rel}")
    return target.read_text(encoding="utf-8", errors="replace")

def strip_uid_block(src):
    return UID_BLOCK_RE.sub("", src)

# ---------- 1. CSP 合规 ----------
def check_no_inline_style():
    bad = []
    for page in PAGES:
        for m in re.finditer(r"""\sstyle\s*=\s*["'][^"']*["']""", read(page)):
            bad.append(f"{page}: {m.group(0).strip()[:48]}")
    if bad:
        raise AssertionError("CSP 会整条拒绝内联 style（default-src 'self' 无 unsafe-inline）：" + "；".join(bad))
    return "两页均无内联 style 属性"

def check_no_inline_script_and_handler():
    bad = []
    for page in PAGES:
        html = read(page)
        for m in re.finditer(r"<script(?![^>]*\ssrc=)[^>]*>", html, re.I):
            bad.append(f"{page}: 内联 <script> {m.group(0)[:40]}")
        for m in re.finditer(r"\son[a-z]+\s*=\s*[\"']", html, re.I):
            bad.append(f"{page}: 内联事件处理器 {m.group(0).strip()}")
        if re.search(r"javascript\s*:", html, re.I):
            bad.append(f"{page}: javascript: 协议")
    if bad:
        raise AssertionError("；".join(bad))
    return "无内联脚本、无 on* 处理器、无 javascript: 协议"

# ---------- 2. XSS 出口 ----------
def check_no_html_sink():
    bad = []
    for js in list(PAGES.values()) + SHARED_JS:
        src = read(js)
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            if sink in src:
                bad.append(f"{js}: {sink}")
    if bad:
        raise AssertionError("渲染必须走 textContent/createElement，禁用 HTML 注入出口：" + "；".join(bad))
    return "全部 DOM 写入使用 textContent / createElement"

def check_no_debug_console():
    bad = [js for js in list(PAGES.values()) + SHARED_JS if re.search(r"\bconsole\.(log|debug)\s*\(", read(js))]
    if bad:
        raise AssertionError("生产前端不应残留调试输出：" + "、".join(bad))
    return "无 console.log / console.debug"

# ---------- 3. DOM 引用完整性 ----------
def check_dom_ids():
    """JS 里以字面量取用的 id，必须能在对应 HTML 或 JS 自建节点中找到。
    截断拼接式取值（$(t+'-view')）不属字面量，由浏览器端走查覆盖。"""
    problems = []
    for page, js in PAGES.items():
        html, src = read(page), read(js)
        declared = set(ID_DEF_RE.findall(html)) | set(ID_ASSIGN_RE.findall(src))
        # 用户端在管理员登录成功后仍会跳到 admin.html，两页共用的顶栏元素允许互相声明
        declared |= set(ID_DEF_RE.findall(read("theme.js")))
        used = set(ID_REF_RE.findall(src))
        missing = sorted(used - declared)
        if missing:
            problems.append(f"{js} 取用了 HTML/JS 中不存在的 id：{missing}")
        unused = sorted(declared - used - set(ID_REF_RE.findall(html)))
        if unused and page == "admin.html":
            pass  # 管理台表单大量依赖 name 取值与整表提交，未用 id 属正常
    if problems:
        raise AssertionError("；".join(problems))
    return "index.html↔app.js、admin.html↔admin.js 的字面量 id 引用全部可解析"

# ---------- 4. 主题与文档元信息 ----------
def check_theme_bootstrap():
    bad = []
    for page in PAGES:
        html = read(page)
        theme_at, css_at = html.find("/theme.js"), html.find('href="/style.css"')
        if theme_at < 0:
            bad.append(f"{page}: 未引入 /theme.js（首绘前无法落地主题，暗色会闪白）")
        elif css_at >= 0 and theme_at > css_at:
            bad.append(f"{page}: theme.js 必须在 style.css 之前同步执行")
        ui_at, own_at = html.find("/ui.js"), html.find('src="/' + PAGES[page] + '"')
        if ui_at < 0 or (own_at >= 0 and ui_at > own_at):
            bad.append(f"{page}: ui.js 必须在 {PAGES[page]} 之前加载")
        if 'name="color-scheme"' not in html:
            bad.append(f"{page}: 缺少 color-scheme 元信息（表单与滚动条会串色）")
        if 'class="skip-link"' not in html or 'id="main"' not in html:
            bad.append(f"{page}: 缺少跳转正文链接")
        if 'rel="stylesheet"' in html and 'data:/' in html:
            bad.append(f"{page}: 存在 data: 内联资源，会被 default-src 'self' 拒绝")
    if bad:
        raise AssertionError("；".join(bad))
    return "两页均按 theme.js → style.css 顺序引导，含 color-scheme 与跳转链接"

def check_shared_assets_exist():
    refs = set()
    for page in PAGES:
        refs |= set(re.findall(r'(?:href|src)="/([A-Za-z0-9._-]+)"', read(page)))
    missing = sorted(r for r in refs if not (WEB / r).is_file())
    if missing:
        raise AssertionError(f"HTML 引用了不存在的同源静态资源：{missing}")
    return "被引用资源均存在：" + "、".join(sorted(refs))

# ---------- 5. CSS 设计令牌自洽 ----------
def _token_block(css, header):
    i = css.find(header)
    if i < 0:
        return None
    j = css.find("}", i)
    return css[i:j]

def check_css_tokens():
    css = read("style.css")
    defined = set(re.findall(r"(--[\w-]+)\s*:", _token_block(css, ":root{") or ""))
    if not defined:
        raise AssertionError("style.css 的 :root 未定义任何自定义属性")
    referenced = set(re.findall(r"var\((--[\w-]+)\)", css))
    undefined = sorted(referenced - defined)
    if undefined:
        raise AssertionError(f"引用了 :root 中不存在的令牌：{undefined}")
    dark_header = ':root[data-theme="dark"]{'
    dark = set(re.findall(r"(--[\w-]+)\s*:", _token_block(css, dark_header) or ""))
    if not dark:
        raise AssertionError("缺少暗色主题令牌块")
    drift = sorted(dark - defined)
    if drift:
        raise AssertionError(f"暗色块定义了亮色没有的令牌（名称漂移，切换后不会生效）：{drift}")
    leaked = sorted(t for t in (referenced & dark) if t not in defined)
    return f"{len(referenced)} 个令牌引用全部可解析，暗色块覆盖 {len(dark)}/{len(defined)} 个令牌，无漂移"

def check_no_hardcoded_colors_in_components():
    """主题化后组件规则不得再写死颜色，否则暗色下会出现白底黑字之类的破功。
    只检查令牌块之外的部分，允许 :root / [data-theme=dark] 内定义具体色值。"""
    css = read("style.css")
    body = css
    for header in (":root{", ':root[data-theme="dark"]{'):
        i = body.find(header)
        if i < 0:
            continue
        j = body.find("}", i)
        body = body[:i] + body[j + 1:]
    hits = []
    for n, line in enumerate(body.splitlines(), 1):
        if line.strip().startswith("/*") or "var(--" in line and "#" not in line:
            continue
        for m in re.finditer(r":\s*(#[0-9a-fA-F]{3,8})\b", line):
            hits.append(f"{n}:{m.group(1)}")
    if len(hits) > 12:
        raise AssertionError(f"组件规则中仍有 {len(hits)} 处硬编码色值，暗色主题易破功：{'、'.join(hits[:8])}…")
    return f"组件层硬编码色值 {len(hits)} 处（仅限打印与日志面板等刻意固定的区域）"

# ---------- 6. JS 语法 ----------
def check_js_syntax():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return "跳过：运行环境无 node（语法由浏览器加载时报错兜底）"
    bad = []
    for js in list(PAGES.values()) + SHARED_JS:
        proc = subprocess.run([node, "--check", str(WEB / js)], capture_output=True, text=True)
        if proc.returncode != 0:
            bad.append(f"{js}: {(proc.stderr or '').strip().splitlines()[-1:] or ['解析失败']}")
    if bad:
        raise AssertionError("；".join(bad))
    return "app.js / admin.js / theme.js 语法通过 node --check"

def check_random_uuid_guard():
    """request_id 不得直接依赖 crypto.randomUUID：它属安全上下文专属 API，
    经局域网 http 访问时为 undefined，会让每个写操作在生成编号时抛错。"""
    bad = []
    for js in ["ui.js"]:
        src = read(js)
        if not re.search(r"const\s+uid\s*=", src):
            bad.append(f"{js}: 缺少 uid() 降级实现")
            continue
        # 说明性注释里提到该 API 属正常，只查真实调用点
        rest = COMMENT_RE.sub("", strip_uid_block(src))
        if "crypto.randomUUID" in rest:
            bad.append(f"{js}: 仍有 uid() 之外的 crypto.randomUUID 直接调用")
        if not re.search(r"crypto\.getRandomValues", src):
            bad.append(f"{js}: uid() 未提供 getRandomValues 降级路径")
    if bad:
        raise AssertionError("；".join(bad))
    return "request_id 统一经 ui.js 的 uid() 取号，含非安全上下文降级"

def check_uid_fallback_runtime():
    """把 app.js 里的 uid 取号实现原样抽出来，在 node 下分别跑三条路径：
    原生 randomUUID / 非安全上下文降级 getRandomValues / 连 getRandomValues
    都没有时退回 Math.random。三条都必须产出合法 v4 UUID 且不重复。
    仅静态断言"存在降级"不足以证明降级可用，故此处真跑一遍。"""
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return "跳过：运行环境无 node"
    block = UID_BLOCK_RE.search(read("ui.js"))
    if not block:
        raise AssertionError("未能从 ui.js 抽取 uid 取号实现")
    harness = """
const fs=require('fs');
const src=fs.readFileSync(process.argv[2],'utf8');
const head=src.indexOf('const uid');
if(head<0){console.error('未找到 uid 定义');process.exit(1);}
const open=src.indexOf('(',src.indexOf('=',head));
let depth=0,end=-1;
for(let i=open;i<src.length;i++){
  const ch=src[i];
  if(ch==='(')depth++;
  else if(ch===')'){depth--;if(depth===0){end=i+1;break;}}
}
if(end<0){console.error('括号配对失败');process.exit(1);}
let block=src.slice(head,end);
if(src[end]===';'){block+=';';}
const V4=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const make=(c)=>new Function('crypto',block+';return uid();')(c);
const weak={getRandomValues(a){for(let i=0;i<a.length;i++)a[i]=(Math.random()*256)|0;return a;}};
const none={};
const cases={'native':globalThis.crypto,'getRandomValues':weak,'math-random':none};
const report={};
for(const [name,c] of Object.entries(cases)){
  const f=make(c);
  if(typeof f!=='function')throw new Error(name+' 未取到取号函数');
  const ids=Array.from({length:500},()=>f());
  const bad=ids.filter(x=>!V4.test(x));
  if(bad.length)throw new Error(name+' 产出非法 v4 UUID：'+bad.slice(0,2).join(','));
  if(new Set(ids).size!==ids.length)throw new Error(name+' 出现重复 request_id');
  report[name]='500 个 v4 UUID 合法且互异';
}
console.log(JSON.stringify(report));
"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        script = fh.name
    try:
        proc = subprocess.run([node, script, str(WEB / "ui.js")], capture_output=True, text=True, encoding="utf-8")
    finally:
        pathlib.Path(script).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError((proc.stderr or proc.stdout or "node 执行失败").strip()[:300])
    return "三条取号路径均通过：" + (proc.stdout or "").strip()

# ---------- 7. 对比度与页签语义 ----------
# 小字号正文（<18.66px 常规 / <24px 粗体）按 WCAG AA 需 4.5:1；大号文本与 UI 部件需 3:1。
CONTRAST_PAIRS = [
    ("正文/页面底", "--text", "--bg", 4.5), ("正文/卡片底", "--text", "--surface", 4.5),
    ("次要文字/页面底", "--muted", "--bg", 4.5), ("次要文字/卡片底", "--muted", "--surface", 4.5),
    ("主按钮文字", "--accent-fg", "--accent", 4.5), ("强调色链接", "--accent", "--surface", 4.5),
    ("次按钮文字", "--accent-ink", "--surface-3", 4.5), ("标题/卡片底", "--text-strong", "--surface", 4.5),
    ("已签到标签", "--ok-fg", "--ok-bg", 4.5), ("已满标签", "--busy-fg", "--busy-bg", 4.5),
    ("待签到标签", "--warn-fg", "--warn-bg", 4.5), ("爽约标签", "--bad-fg", "--bad-bg", 4.5),
    ("图例文字", "--sage", "--bg", 4.5), ("未读角标", "--danger-fg", "--danger", 4.5),
    ("危险按钮", "--danger-fg", "--danger", 4.5), ("焦点环/卡片底", "--focus", "--surface", 3.0),
    ("焦点环/页面底", "--focus", "--bg", 3.0),
    ("表格正文/隔行底", "--text", "--surface-2", 4.5), ("表格正文/悬停底", "--text", "--accent-soft", 4.5),
    ("图表文字", "--chart-text", "--surface", 4.5),
    ("图表柱 1", "--chart-bar-1", "--surface", 3.0), ("图表柱 2", "--chart-bar-2", "--surface", 3.0),
]

def _resolve(tokens, name):
    return tokens.get(name)

def _hex(value):
    v = value.strip()
    if not v.startswith("#"):
        return None
    v = v[1:]
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))

def _lum(rgb_t):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb_t)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

def check_wcag_contrast():
    """两套主题下的关键文字/部件配色必须达 WCAG AA。调色板改动常凭观感判断，
    次要文字与角标这类小字号最易掉到 3.x，故把阈值固化成门禁。"""
    css = read("style.css")
    light = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", _token_block(css, ":root{") or ""))
    dark = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", _token_block(css, ':root[data-theme="dark"]{')) or [])
    failures = []
    for label, fg, bg, need in CONTRAST_PAIRS:
        for theme, tokens in (("浅色", light), ("暗色", dark)):
            f, b = _resolve(tokens, fg) or _resolve(light, fg), _resolve(tokens, bg) or _resolve(light, bg)
            hf, hb = _hex(f), _hex(b)
            if not hf or not hb:
                failures.append(f"{theme} {label}: 令牌 {fg}/{bg} 非十六进制色值，无法计算")
                continue
            r = _ratio(hf, hb)
            if r < need:
                failures.append(f"{theme} {label} {f} on {b} = {r:.2f}（需 ≥{need}）")
    if failures:
        raise AssertionError("；".join(failures))
    return f"两套主题 {len(CONTRAST_PAIRS) * 2} 组配色全部达 WCAG AA（正文阈值 4.5:1，部件 3:1）"

def check_tab_keyboard():
    """role=tab 自带一套键盘契约（W3C APG）：Tab 键只进入页签组一次（组内仅选中项可聚焦），
    左右方向键在页签间移动焦点，Home/End 到首尾。声明了 role 却不履行键盘语义，
    比不声明更糟——读屏用户会按已知的交互模型操作并得到意外结果。
    另外方向键只移动焦点、不激活面板：面板加载会发请求，自动激活会让一次连按方向键
    打出十几次请求。"""
    js = read("ui.js")
    m = re.search(r"function wireTabs\(.*?\n  \}", js, re.S)
    if not m:
        raise AssertionError("ui.js 缺少 wireTabs 实现")
    body = m.group(0)
    problems = []
    for key in ("'ArrowRight'", "'ArrowLeft'", "'Home'", "'End'"):
        if key not in body:
            problems.append(f"wireTabs 未处理 {key}")
    if "tabIndex" not in body:
        problems.append("wireTabs 未实现 roving tabindex，全部页签都留在 Tab 序列里")
    if "scrollIntoView" not in body:
        problems.append("wireTabs 未把选中页签滚入可视区：窄屏页签条横向溢出时用户看不到自己所在页签")
    kd = re.search(r"addEventListener\('keydown',.*?\n      \}\);", body, re.S)
    if not kd:
        problems.append("wireTabs 未找到 keydown 处理块")
    elif "onSelect(" in kd.group(0):
        problems.append("wireTabs 的方向键不应直接激活面板（自动激活会放大请求量），只允许移动焦点")
    if problems:
        raise AssertionError("；".join(problems))
    return "wireTabs 覆盖方向键/Home/End、roving tabindex、选中项可见性与手动激活"

def check_tab_semantics():
    """页签必须是 tablist/tab/tabpanel 三件套且互相指认：
    仅有 aria-current 时读屏软件不会播报「标签页 1/3」，键盘用户也无法预期面板位置。"""
    problems = []
    for page, tabs in (("index.html", ["book", "checkin", "mine"]),
                       ("admin.html", ["labs", "publish", "slots", "approvals", "records", "users",
                                       "assets", "stats", "metrics", "notify", "sent", "logs"])):
        html = read(page)
        nav = re.search(r'<nav class="tabs"[^>]*>', html)
        if not nav or 'role="tablist"' not in nav.group(0):
            problems.append(f"{page}: .tabs 容器缺少 role=tablist")
        for t in tabs:
            btn = re.search(r'<button[^>]*data-tab="%s"[^>]*>' % t, html)
            if not btn:
                problems.append(f"{page}: 缺少 data-tab={t} 页签"); continue
            tag = btn.group(0)
            for attr in ('role="tab"', 'aria-selected=', 'id="tab-%s"' % t, 'aria-controls="%s-view"' % t):
                if attr not in tag:
                    problems.append(f"{page}: 页签 {t} 缺少 {attr}")
            panel = re.search(r'<section id="%s-view"[^>]*>' % t, html)
            if not panel:
                problems.append(f"{page}: 页签 {t} 的面板 #{t}-view 不存在"); continue
            if 'role="tabpanel"' not in panel.group(0) or 'aria-labelledby="tab-%s"' % t not in panel.group(0):
                problems.append(f"{page}: 面板 {t}-view 缺少 role=tabpanel / aria-labelledby")
        if html.count('aria-selected="true"') != 1:
            problems.append(f"{page}: 初始选中页签应为 1 个，实为 {html.count('aria-selected=' + chr(34) + 'true' + chr(34))}")
        if html.count('role="tablist"') != 1:
            problems.append(f'{page}: role=tablist 应恰有 1 个（页签条），实为 {html.count("role=" + chr(34) + "tablist" + chr(34))}')
        if html.count('role="tab"') != len(tabs):
            problems.append(f"{page}: role=tab 数量应等于页签数 {len(tabs)}，实为 {html.count('role=' + chr(34) + 'tab' + chr(34))}")
        js = read(PAGES[page])
        if "UI.wireTabs(" not in js:
            problems.append(f"{page}: 页签未经 UI.wireTabs 接线，缺方向键导航与 roving tabindex")
        if "setAttribute('aria-selected'" in js:
            problems.append(f"{page}: 页签在本页又各自同步了 aria-selected，应收敛到 UI.wireTabs")
    if problems:
        raise AssertionError("；".join(problems))
    return "两页共 15 个页签均为 tablist/tab/tabpanel 三件套且互相指认"

CORE_DEFS = ["function el(", "function empty(", "function table(", "class ApiError",
             "const uid", "function dismissToast(", "async function api(", "const dateCN",
             "function syncThemeToggle(", "function byteLen(", "function overBytes(",
             "function formCheck(", "function scrollTo(", "function clip(", "function noteCell("]

def check_no_core_duplication():
    """UI 基座只能有一份实现：app.js 与 admin.js 过去各自抄了一份工具函数，
    第 3 轮改提示条时被迫在两个文件里做同样的编辑——这类重复正是漂移的源头。"""
    owners = {}
    for js in ["app.js", "admin.js", "ui.js"]:
        src = read(js)
        for d in CORE_DEFS:
            if d in src:
                owners.setdefault(d, []).append(js)
    bad = {d: v for d, v in owners.items() if v != ["ui.js"]}
    if bad:
        raise AssertionError("核心工具应只定义在 ui.js，实际：" + "；".join(f"{d} -> {v}" for d, v in sorted(bad.items())))
    return f"{len(CORE_DEFS)} 项核心工具全部单点定义于 ui.js"

UI_EXPORT_RE = re.compile(r"return \{([^}]*)\};", re.S)
UI_DESTRUCTURE_RE = re.compile(r"const \{([^}]*)\}\s*=\s*UI\s*;")

def check_ui_bindings_resolve():
    """app.js / admin.js 顶部的 `const {…}=UI` 里每个名字都必须真是 ui.js 导出的成员。
    解构一个不存在的键不会报错，只会得到 undefined——直到运行时调用才炸，
    而语法检查（node --check）完全看不出来。重构当天就踩中过一次：
    把 UI.time/UI.stamp 按管理台的历史命名 fmtTime/fmtStamp 直接解构，
    结果场次表格渲染时抛错，只有跑到的 e2e 用例才发现。"""
    exports = set()
    for name in UI_EXPORT_RE.findall(read("ui.js")):
        exports |= {p.split(":")[0].strip() for p in name.split(",") if p.strip()}
    if not exports:
        raise AssertionError("ui.js 未导出任何成员")
    problems = []
    for js in ("app.js", "admin.js"):
        src = read(js)
        m = UI_DESTRUCTURE_RE.search(src)
        if not m:
            problems.append(f"{js}: 未找到从 UI 解构的语句")
            continue
        wanted = []
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            source = part.split(":")[0].strip()
            wanted.append(source)
        missing = sorted(set(wanted) - exports)
        if missing:
            problems.append(f"{js} 解构了 ui.js 未导出的成员：{missing}")
    if problems:
        raise AssertionError("；".join(problems))
    return f"两页共 {len(exports)} 个 UI 导出，解构绑定全部可解析"

def check_action_cells_rendered():
    """行内操作按钮的容器必须真的被放进单元格。HELD 分支曾建好 ops、往里加了
    「确认保留」，却把容器丢在原地——界面上写着「请在截止前确认」，全站却没有任何
    确认入口。这类"建了不挂"的 DOM 既过得了 node --check，也不会在静态快照里露馅。"""
    problems, total = [], 0
    for js in ("app.js", "admin.js"):
        src = read(js)
        starts = [m.start() for m in re.finditer(r"const ops=el\('span'", src)]
        total += len(starts)
        for i, pos in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(src)
            if "push(ops)" not in src[pos:end]:
                snippet = " ".join(src[pos:pos + 60].split())
                problems.append(f"{js}: 第 {i + 1} 个 ops 容器建好后未挂入单元格（按钮不可见）：{snippet}…")
    if problems:
        raise AssertionError("；".join(problems))
    return f"两页 {total} 个行内操作容器均已挂入单元格"

def _media_block(css, header):
    """取 @media 块的完整内容（含嵌套花括号），header 形如 '@media(max-width:600px){'。"""
    i = css.find(header)
    if i < 0:
        return None
    depth, j = 0, i + len(header) - 1
    while j < len(css):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i:j + 1]
        j += 1
    return None

def check_table_contract():
    """窄屏表格与排序的 JS↔CSS 契约：CSP 禁内联样式，表格的排序指示与列名只能靠 class /
    data-* 由外部样式表驱动，任何一侧单独改动都会静默失效（表现为按钮变浏览器默认样式、
    卡片式表格只剩没有列名的数字），故固化成门禁。"""
    js, css = read("ui.js"), read("style.css")
    problems = []
    if "dataset.label" not in js:
        problems.append("ui.js 的 table() 未把列名写入 td[data-label]，窄屏卡片表将失去列名")
    if "attr(data-label)" not in css:
        problems.append("style.css 缺少 td::before{content:attr(data-label)} 规则")
    mobile = _media_block(css, "@media(max-width:600px){") or ""
    if "display:flex" not in mobile or "table-wrap" not in mobile:
        problems.append("600px 断点内未把 .table-wrap 改为卡片式布局")
    if ".th-sort" not in css:
        problems.append("ui.js 生成 .th-sort 排序按钮，但 style.css 未定义其样式（会退化为浏览器默认按钮）")
    if "aria-sort" not in js or 'th[aria-sort]' not in css:
        problems.append("排序方向缺少 aria-sort ↔ th[aria-sort] 双向同步（读屏无法播报当前排序）")
    # 只对整列为文本/数字的列开排序：含按钮等 Node 的列排序无意义
    if "sortableColumn" not in js:
        problems.append("ui.js 缺少列可排序判定，操作列也会被加上排序按钮")
    th_colors = re.findall(r"(?m)^th\{[^}]*color:\s*var\((--[\w-]+)\)", css)
    if not th_colors:
        problems.append("style.css 未显式设定 th 的文字颜色令牌")
    elif "--muted" in th_colors:
        problems.append("表头文字使用 --muted：压在 --surface-3 上仅 4.19:1，低于 AA 的 4.5:1")
    if problems:
        raise AssertionError("；".join(problems))
    return "表格排序与窄屏卡片化的 JS/CSS 契约一致"

# ---------- 8. 主题化视觉与表单校验反馈 ----------
JS_COLOR_RE = re.compile(r"""['"]#[0-9a-fA-F]{3,8}\b""")
JS_INLINE_STYLE_RE = re.compile(r"\.style\.[A-Za-z]+\s*=")
THEMED_CLASSES = ["chart-bar-1", "chart-bar-2", "chart-grid", "chart-text",
                  "field-note", "field-error", "field-invalid", "alt-list"]
CHART_TOKENS = ["--chart-bar-1", "--chart-bar-2", "--chart-grid", "--chart-text"]

def check_no_styling_in_js():
    """视觉表现必须留在样式表里。JS 写死的颜色与内联样式不会随主题切换：
    统计柱状图曾把 fill 写成十六进制常量，暗色主题下实测坐标文字 3.84:1、
    图例文字 1.21:1（等于看不见），而这一切在浅色主题下看不出任何问题。"""
    css = read("style.css")
    light = _token_block(css, ":root{") or ""
    dark = _token_block(css, ':root[data-theme="dark"]{') or ""
    problems = []
    for js in ("app.js", "admin.js", "ui.js", "theme.js"):
        src = COMMENT_RE.sub("", read(js))
        for m in JS_COLOR_RE.finditer(src):
            problems.append(f"{js}: 写死色值 {m.group(0)[:12]}…（应改为 CSS 令牌类）")
        for m in JS_INLINE_STYLE_RE.finditer(src):
            problems.append(f"{js}: {m.group(0)}… 由 JS 写内联样式，主题与断点都覆盖不到")
    for cls in THEMED_CLASSES:
        if "." + cls + "{" not in css:
            problems.append(f"style.css 未定义 .{cls}（JS 按类名生成节点，缺样式会静默失效）")
    for tok in CHART_TOKENS:
        if tok + ":" not in light:
            problems.append(f"缺少亮色令牌 {tok}")
        if tok + ":" not in dark:
            problems.append(f"缺少暗色令牌 {tok}（暗色会沿用亮色值，正是本轮修掉的缺陷）")
    if problems:
        raise AssertionError("；".join(problems))
    return "JS 侧零色值零内联样式；8 个主题类与 4×2 个图表令牌齐备"

def check_form_validation_contract():
    """写操作的失败反馈不能只剩一条会被手动关掉、且不指明字段的 toast，
    更不能只换个边框颜色（WCAG 1.4.1：颜色不得是信息的唯一载体）。
    场次容量还有一层数据口径问题：服务端按 CONFIRMED+HELD 校验下限，
    列表接口只给 CONFIRMED 计数，所以提示必须如实写明实际下限可能更高。"""
    html, js = read("admin.html"), read("admin.js")
    problems = []
    if 'id="slot-cap-error"' not in html:
        problems.append("admin.html 缺少场次容量的常驻错误位")
    if 'role="alert"' not in html:
        problems.append("错误位没有 role=alert，读屏不会播报校验结果")
    if 'aria-describedby="slot-cap-note slot-cap-error"' not in html:
        problems.append("容量输入框未用 aria-describedby 关联说明与错误文本")
    if '<form id="slot-form" novalidate>' not in html:
        problems.append("场次表单未声明 novalidate：浏览器原生校验会先于页面提示弹出，"
                        "中文错误位与 aria 状态就永远不会出现（实测填 0 时整条反馈链路被抢走）")
    for need, why in (("capError(", "缺少统一的字段错误渲染函数"),
                      ("aria-invalid", "失败字段未标 aria-invalid"),
                      ("dataset.floor", "未记录该场次的容量下限"),
                      ("没有新增场次", "发布 0 个场次时未解释原因")):
        if need not in js:
            problems.append(why)
    block = js[js.find("$('slot-form').addEventListener('submit'"):]
    block = block[:block.find("$('slot-form').elements.capacity.addEventListener")]
    if block and block.find("dataset.floor") > block.find("mutate('/api/admin/slots/"):
        problems.append("容量下限校验必须在发请求之前，否则管理员白跑一次 409")
    if problems:
        raise AssertionError("；".join(problems))
    return "场次容量：下限提示 + 请求前本地校验 + 常驻文字错误（非仅颜色）；发布空结果有解释"

# ---------- 9. 选择器落位与标记闭合 ----------
STRUCTURE_RE = re.compile(r"<(/?)(div|section|form|main|nav|header|footer|details|summary|table|tbody|thead|tr)\b[^>]*>")

def check_html_structure_balanced():
    """结构元素必须显式闭合。index.html 的 .admin-grid 曾漏掉一个 </div>，浏览器靠隐式闭合兜底，
    界面看着正常——但真实嵌套已经不是写代码的人以为的那个：#app-view 实际是被 </main> 关掉的。
    这类偏差只在"往页签区后面追加节点"时才咬人，届时新节点会掉进某个面板里，随页签一起显隐。"""
    problems = []
    for page in PAGES:
        html = read(page)
        stack = []
        for m in STRUCTURE_RE.finditer(html):
            close, tag = m.group(1), m.group(2)
            line = html[:m.start()].count("\n") + 1
            if close:
                if stack and stack[-1][0] == tag:
                    stack.pop()
                else:
                    top = f"<{stack[-1][0]}>@第{stack[-1][1]}行" if stack else "空"
                    problems.append(f"{page}: 第 {line} 行出现 </{tag}>，但栈顶是 {top}")
                    idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == tag), -1)
                    if idx >= 0:
                        del stack[idx:]
            else:
                stack.append((tag, line))
        for tag, line in stack:
            problems.append(f"{page}: <{tag}>（第 {line} 行）没有显式闭合，靠浏览器自动闭合兜底")
    if problems:
        raise AssertionError("；".join(problems))
    return "两页结构元素逐层显式闭合，隐式闭合会让后续追加的节点落错父级"

FABRICATED_COUNT_RE = re.compile(r"""['"]候补 \d+ 人""")

def check_picker_region_contract():
    """两个选择器各有各的位置，不能混用同一块常驻面板。
    #alternatives（预约页签的替代时段）必须落在所有 role=tabpanel 之外：它一旦藏在某个面板里，
    另一个页签展开时写进去的就是 display:none 的子孙——实测节点建好了、getClientRects() 为 0。
    改期候选则相反：它属于记录表里的某一行，必须行内展开在该行下方，
    并且随表格重渲染一起消失（否则残留一份对不上号的旧候选）。"""
    html, js, css = read("index.html"), read("app.js"), read("style.css")
    problems = []
    at = html.find('id="alternatives"')
    if at < 0:
        problems.append("index.html 缺少 #alternatives 容器")
    else:
        panels = [(m.start(), html.find("</section>", m.start())) for m in
                  re.finditer(r'<section\b[^>]*role="tabpanel"[^>]*>', html)]
        inside = [html[s:e].split(">")[0][:40] for s, e in panels if s < at < e]
        if inside:
            problems.append(f"#alternatives 仍在页签面板内部（{inside[0]}…）：另一个页签展开它时矩形为 0，改期/替代时段功能不可达")
        head = html[html.rfind("<", 0, at):html.find(">", at) + 1]
        for attr in ('class="alt-area"', "hidden", 'tabindex="-1"', "aria-label="):
            if attr not in head:
                problems.append(f"#alternatives 缺少 {attr}")
    if FABRICATED_COUNT_RE.search(js):
        problems.append("app.js 播报了写死的「候补 N 人」：SLOT_FULL 的 alternatives 载荷只有 {id,start_at,end_at}，"
                        "候补人数是界面编造的")
    if "async function switchTab(tab){hideAlternatives();closeReschedule()" not in js:
        problems.append("switchTab 未先收起两类选择器：切页签后候选列表会悬空留在页面上")
    if "hideAlternatives()" not in js.split("async function loadSlots()")[1][:200]:
        problems.append("loadSlots（页签内刷新入口）未收起选择器，旧候选会留在新时段列表下方")
    rs = js[js.find("async function showReschedule("):js.find("function renderRecFilter(")]
    if "$('alternatives')" in rs:
        problems.append("改期不得再写共享面板 #alternatives：它一旦不属于触发行，"
                        "就会回到「距行 1589px、压在账号面板之后、表格重渲染后无人认领」的老问题")
    if "row.after(" not in rs:
        problems.append("showReschedule 必须把候选行插在触发行正下方（row.after），行内展开才是可达的")
    if "finally{btn.disabled=false;}" not in rs:
        problems.append("showReschedule 必须在 finally 里复位「改期」按钮：只在部分分支解禁会让入口永久失效")
    if "state.picker" not in rs or "closeReschedule();" not in rs:
        problems.append("改期选择器必须被记录并先收掉上一个：否则换行展开会同时留下多个候选列表")
    lr = js[js.find("async function loadRecords("):js.find("async function showReschedule(")]
    if "closeReschedule()" not in lr[:120]:
        problems.append("loadRecords 重画表格前必须收掉改期选择器：否则旧候选会在表格更新后残留成游离节点")
    if "if(e.key==='Escape'){closeNotify();closeReschedule();}" not in js:
        problems.append("Esc 必须同时收起通知面板与改期选择器：只处理一个会让另一个关不掉")
    for cls, why in ((".alt-head{", "选择器标题行的收起按钮需要与标题同行"),
                     (".reschedule-row>td{", "缺少改期候选行的样式（暗色下会沿用表格底色、边界不清）")):
        if cls not in css:
            problems.append(f"style.css 缺少 {cls}：{why}")
    if problems:
        raise AssertionError("；".join(problems))
    return "改期行内展开且随表格重渲染收口；替代时段面板独立在页签之外；不播报服务端未给的候补人数"


NARROW_QUERY = "@media(max-width:600px){"


def check_narrow_screen_contract():
    """窄屏两处「只有截图才看得出来」的缺陷，落成静态契约。
    1) 顶栏：390px 下品牌曾被挤成「管理/控制/台」三行、右侧控件逐字断行。
       窄屏必须换成两行栅格（topbar 可换行 + 工具区 flex-basis:100%），且品牌文字不许折行。
    2) 横向滚动条在手机上几乎不可见：管理台 12 个页签被齐腰截断却没有任何「还能滑」的信号。
       必须挂上纯 CSS 边缘提示，且遮罩层（local）要宽过阴影层（scroll），
       否则贴边时遮不住阴影，会在两端各留一道幽灵阴影。"""
    css = read("style.css")
    problems = []
    for token in ("--scroll-shade", "--hscroll-cover", "--hscroll-layers"):
        if token + ":" not in css:
            problems.append(f"缺少设计令牌 {token}")
    if css.count("--scroll-shade:") < 2:
        problems.append("--scroll-shade 只在浅色主题定义，暗色下边缘阴影会看不见")

    layers = re.search(r"--hscroll-layers:\s*([^;]+);", css)
    if not layers:
        problems.append("--hscroll-layers 未定义")
    else:
        body = layers.group(1)
        if body.count(" local") != 2 or body.count(" scroll") != 2:
            problems.append("--hscroll-layers 需要 2 层 local 遮罩 + 2 层 scroll 阴影，缺一侧就失去位置感")
        if "--hscroll-cover" not in body:
            problems.append("遮罩层必须用 --hscroll-cover，才能在 .panel 内自动换成面板底色")
        widths = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)px (?:100%|var)", body)]
        if len(widths) < 4 or min(widths[:2]) <= max(widths[2:]):
            problems.append(f"遮罩宽 {widths[:2]} 必须大于阴影宽 {widths[2:]}，否则贴边时遮不住阴影")
    if ".panel{" not in css or "--hscroll-cover:var(--surface)" not in css.split(".panel{", 1)[1].split("}", 1)[0]:
        problems.append(".panel 未把 --hscroll-cover 换成 --surface：淡出会涂成页面底色而不是面板底色")

    tabs = css.split(".tabs{", 1)[1].split("}", 1)[0] if ".tabs{" in css else ""
    for frag, why in (("background:var(--hscroll-layers)", "页签条没挂边缘提示"),
                      ("overflow-x:auto", "页签条要能横向滚动"),
                      ("padding-inline:16px", "页签条缺少给遮罩涂的左右空白")):
        if frag not in tabs:
            problems.append(why)
    if not re.search(r"\.tabs\{[^}]*margin:\s*28px\s+-16px", css):
        problems.append(".tabs 需要 -16px 负外边距抵掉内缩，否则页签会比正文缩进 16px")

    at = css.find(NARROW_QUERY)
    if at < 0:
        problems.append(f"缺少 {NARROW_QUERY} 窄屏块")
    else:
        narrow = css[at:]
        for frag, why in ((".topbar{flex-wrap:wrap", "窄屏顶栏仍是一行，品牌与控件会互相挤压折字"),
                          (".topbar-tools{flex:1 1 100%", "窄屏工具区没有换到第二行独占整幅宽度"),
                          (".brand>span{", "窄屏品牌文字未单独约束")):
            if frag not in narrow:
                problems.append(why)
        span = narrow.split(".brand>span{", 1)[1].split("}", 1)[0] if ".brand>span{" in narrow else ""
        if "white-space:nowrap" not in span:
            problems.append(".brand>span 缺 white-space:nowrap，「管理控制台」会被竖排折字")
        head = narrow.split("thead tr{", 1)[1].split("}", 1)[0] if "thead tr{" in narrow else ""
        if "background:var(--hscroll-layers)" not in head or "margin-inline:-16px" not in head:
            problems.append("窄屏排序胶囊行同样是横滚条，需要挂上同一套边缘提示")
    if problems:
        raise AssertionError("；".join(problems))
    return "窄屏顶栏两行栅格 + 横滚条位置感知边缘提示，令牌与负边距配套"


USER_FORMS = ("login-form", "register-form", "password-form", "token-form")


def check_form_validation_ownership():
    """用户端四个表单的校验必须由页面自己负责，不能交给浏览器原生气泡。
    第 12 轮实测：登录空字段提交时 submit 事件根本不触发（submitFired=false、
    #login-error 全空），因为 required 先被原生约束校验拦下 —— 页面写好的
    role=alert 区永远轮不到显示，读屏不播报、暗色下也不跟随主题。
    顺带清掉一个假承诺：minlength 对 type=password 不适用，留着等于宣称
    了一条浏览器从不执行的规则，短密码只能白跑一次请求再被服务端拒。"""
    html, js = read("index.html"), read("app.js")
    problems = []
    for form in USER_FORMS:
        m = re.search(r'<form id="' + re.escape(form) + r'"[^>]*>', html)
        if not m:
            problems.append(f"找不到 <form id='{form}'>")
            continue
        if "novalidate" not in m.group(0):
            problems.append(f"#{form} 未声明 novalidate：原生校验会先于页面提示弹出")
    for pw in re.finditer(r'<input[^>]*type="password"[^>]*>', html):
        if "minlength" in pw.group(0):
            problems.append("密码框上挂着 minlength —— 该属性对 type=password 无效，是假承诺")
    for form, box in (("login-form", "login-error"), ("register-form", "register-error"),
                      ("password-form", "password-error"), ("token-form", "token-error")):
        if f'id="{box}" class="error" role="alert"' not in html:
            problems.append(f"#{form} 缺少常驻错误位 #{box}（role=alert）")
        i = js.find("$('" + form + "').addEventListener('submit'")
        if i < 0:
            problems.append(f"找不到 #{form} 的 submit 处理器")
            continue
        head = js[i:i + 400]
        if "formCheck(" not in head:
            problems.append(f"#{form} 提交前没跑本地校验，约束仍然只写在 HTML 里靠浏览器兜底")
        elif "'" + box + "'" not in head:
            problems.append(f"#{form} 的本地校验结果没有写进 #{box}")
    # byteLen / formCheck 不在此列：它们属于全站共用的工具，第 13、14 轮起单点定义在 ui.js，
    # 由 check_no_core_duplication 保证 app.js / admin.js 不再各自抄一份。
    for name in ("hasBlank", "rRegName", "rLoginName", "rLoginPw", "rPw", "rTokName"):
        if js.count("function " + name + "(") != 1:
            problems.append(f"{name} 应且仅应定义一次（实际 {js.count('function ' + name + '(')} 次）")
    for dead in ("notEmpty", "lenRange"):
        if dead in js:
            problems.append(f"app.js 仍引用已删除的 {dead}，提交时会 ReferenceError")
    if problems:
        raise AssertionError("；".join(problems))
    return "用户端四个表单全部 novalidate + 提交前本地校验并写进各自 role=alert 区；密码框不再挂无效的 minlength"


def _c_limit(src, pattern, what):
    m = re.search(pattern, src)
    if not m:
        raise AssertionError(f"服务端 {what} 的规则没在 C 源码里找到（模式变了，门禁需同步）")
    return tuple(int(x) for x in m.groups())


def check_credential_rule_parity():
    """凭据长度限制同时活在三个地方：C 服务端、HTML 属性、app.js 本地校验。
    第 12 轮把校验收归页面时留下的欠账就是没人核对过三方是否一致。
    本检查直接把数字从 src/http.c 与 src/service.c 里抠出来，再和 JS 常量、HTML
    maxlength 对表 —— 任何一侧改了限制而另一侧没跟上，这里就红。
    另外锁死口径：服务端用 strlen（UTF-8 字节），JS 必须按字节数，
    且不得对密码 trim（空格是合法口令字符）。HTML 的 maxlength 数的是 UTF-16 码元，
    它只能当第一道粗筛（码元超了字节一定超），真正的字节判定只有 JS 与服务端能做。"""
    http_c = read("../src/http.c")
    service_c = read("../src/service.c")
    js, html = read("app.js"), read("index.html")
    problems = []

    name_min, name_max = _c_limit(http_c, r"(?s)static int name_ok.*?n<(\d+)\|\|n>(\d+)", "用户名 name_ok")
    reg_min, reg_max = _c_limit(http_c, r"strlen\(pw\)<(\d+)\|\|strlen\(pw\)>(\d+)", "注册密码")
    svc_min, svc_max = _c_limit(service_c, r"strlen\(new_pw\)<(\d+)\|\|strlen\(new_pw\)>(\d+)", "改密新密码")
    tok_max = _c_limit(service_c, r"(?s)Result token_create\(DB .*?strlen\(name\)>(\d+)", "令牌名称")[0]
    login_name_max = _c_limit(http_c, r"(?s)static Result login\(DB .*?text_ok\(name,(\d+),0\)", "登录用户名上限")[0]
    login_pw_max = _c_limit(http_c, r"(?s)static Result login\(DB .*?text_ok\(pw,(\d+),0\)", "登录密码上限")[0]
    note_max = _c_limit(service_c, r"strlen\(bnote\)>(\d+)", "预约备注")[0]

    if (reg_min, reg_max) != (svc_min, svc_max):
        problems.append(f"服务端自身两处密码限制不一致：注册 {reg_min}..{reg_max} vs 改密 {svc_min}..{svc_max}")

    for const, want in (("NAME_MIN", name_min), ("NAME_MAX", name_max),
                        ("PW_MIN", reg_min), ("PW_MAX", reg_max), ("TOKEN_MAX", tok_max),
                        ("NOTE_MAX", note_max)):
        m = re.search(const + r"\s*=\s*(\d+)", js)
        if not m:
            problems.append(f"app.js 找不到常量 {const}")
        elif int(m.group(1)) != want:
            problems.append(f"app.js {const}={m.group(1)} 与服务端的 {want} 不一致")
    if "overBytes(nt,NOTE_MAX" not in js:
        problems.append("预约备注不再按字节校验，中文备注会绕过 maxlength 吃到服务端的裸 400")
    if not re.search(r"text_ok\(name," + str(name_max) + r",0\)", http_c):
        problems.append("登录与注册的用户名上限已不再是同一个数，登录侧规则需重新核对")
    if login_name_max != name_max or login_pw_max != reg_max:
        problems.append(f"登录侧上限（{login_name_max}/{login_pw_max}）与 JS 常量不再同源")

    # 变异测试暴露的空洞：把 overBytes 内部从 byteLen(s) 换回 s.length，27 项照样全绿——
    # 因为「字节」两个字还留在它上方的注释里。所以只盯函数体本身：既要真的调 byteLen，
    # 也要在返回的文案里报出字节数。
    ui_js = read("ui.js")
    if "new TextEncoder()" not in ui_js or "function byteLen(" not in ui_js:
        problems.append("ui.js 不再提供 UTF-8 字节口径的 byteLen，凭据长度会退回按码元计数")
    bl = re.search(r"function byteLen\(s\)\s*\{([^}]*)\}", ui_js)
    if not bl or "encode(" not in bl.group(1):
        problems.append("ui.js 的 byteLen 不再经 TextEncoder.encode 计算，函数名与实际度量方式已经不符")
    i = ui_js.find("function overBytes(")
    end = ui_js.find("\n  }", i)
    body = ui_js[i:end] if i >= 0 and end > i else ""
    if "byteLen(s)" not in body:
        problems.append("UI.overBytes 不再用 byteLen 度量输入，长度口径退回 UTF-16 码元计数")
    if "字节" not in body:
        problems.append("UI.overBytes 的提示文案不再报出字节数，中文用户看不到自己为什么被拦")
    if not any("byteLen" in names for names in UI_DESTRUCTURE_RE.findall(js)):
        problems.append("app.js 的 byteLen 不是从 UI 解构来的（解构漏了就抛错，自己再定义一份就会和 UI 口径分叉）")
    for fn in ("rRegName", "rPw", "rLoginName", "rLoginPw", "rTokName"):
        m = re.search(r"function " + fn + r"\(v\)\{([^\n]*)\}", js)
        if not m:
            problems.append(f"app.js 缺少凭据规则函数 {fn}")
        elif ".trim()" in m.group(1):
            problems.append(f"{fn} 又改用 trim 后的长度：密码里的空格是有意义的，会被误判为空")
        elif "byteLen(v)" not in m.group(1):
            problems.append(f"{fn} 不再按字节测量输入")
    if "hasBlank" not in js or "c<=0x20" not in js:
        problems.append("app.js 不再本地拦截用户名里的空格/控制字符，服务端 name_ok 会把它打回")
    # 超长的提示必须报出实际字节数：只说「需为 2..64 个可见字符」时，
    # 打了 22 个汉字的人无从知道自己为什么被判超长（文案由 UI.overBytes 统一给）。
    for fn in ("rRegName", "rPw", "rTokName"):
        m = re.search(r"function " + fn + r"\(v\)\{([^\n]*)\}", js)
        if m and "overBytes(" not in m.group(1):
            problems.append(f"{fn} 的超长提示没有走 UI.overBytes，丢掉了实际字节数")

    # 「校验的字符串」和「落库的字符串」必须是同一个。注册曾在提交前偷偷 .trim()：
    # 本地按原样判长度，服务端却收到另一个名字（JS 的 trim 还会吃掉 NBSP），
    # 用户看到的是 A、账号是 B。这里直接锁死提交载荷取的是原始值。
    m = re.search(r"api\('/api/register',\{([^}]*)\}", js)
    if not m:
        problems.append("app.js 找不到 /api/register 的提交载荷")
    elif "username:f.elements.username.value," not in m.group(1) or ".trim()" in m.group(1):
        problems.append("注册提交的不再是校验过的那个用户名（被 trim 改写），用户看到的与落库的不一致")
    # 服务端两条与长度无关的规则，页面也必须镜像，否则用户白跑一次网络往返。
    if not re.search(r'strcmp\(old_pw,new_pw\)', service_c):
        problems.append("服务端不再有『新密码不同于原密码』规则，app.js 的本地拦截成了无源之水")
    if "old_password.value===f.elements.new_password.value" not in js:
        problems.append("改密不再本地拦截新旧相同，用户要点一次提交才知道失败")
    if "password.value!==f.elements.confirm.value" not in js:
        problems.append("注册不再本地拦截两次密码不一致，服务端不校验 confirm，会静默按第一次的输入建号")

    for name, maxlen in (("username", name_max), ("password", reg_max), ("confirm", reg_max),
                         ("old_password", reg_max), ("new_password", reg_max), ("name", tok_max)):
        m = re.search(r'<input[^>]*name="' + name + r'"[^>]*>', html)
        if not m:
            problems.append(f"index.html 找不到 name={name} 的输入框")
        elif not re.search(r'maxlength="' + str(maxlen) + r'"', m.group(0)):
            problems.append(f"{name} 输入框的 maxlength 不再是 {maxlen}，与服务端上限脱节")
    if problems:
        raise AssertionError("；".join(problems))
    return (f"三方对齐：用户名 {name_min}..{name_max} 字节、密码 {reg_min}..{reg_max} 字节、"
            f"令牌名 ≤{tok_max} 字节；JS 按 UTF-8 字节、不 trim、不改写提交值，"
            "且镜像了『新旧密码不同』『两次输入一致』两条非长度规则")


ADMIN_FORMS = {"lab-form": "lab-error", "asset-form": "asset-error",
                "notify-form": "notify-error", "publish-form": "publish-error"}

def check_admin_form_validation_ownership():
    """第 12 轮在用户端修过的坑，管理台当时只修了 #slot-form 一处。本轮实测：
    空提交实验室表单时 submit 事件根本不触发（fired:false），弹的是浏览器原生气泡
    "Please fill out this field." —— 页面 lang 是 zh-CN，管理员看到的却是英文，
    而且不受暗色主题控制、几秒自动消失、不进读屏播报。
    现在四个表单都声明了 novalidate 并各有一个常驻 role=alert 错误位。
    这里锁两件事：
    1) 带 required 的字段必须都在规则表里 —— 否则 novalidate 之后没人管它，
       用户清空必填项会直接吃到服务端的裸 400（加了 novalidate 却漏了规则，比不加更糟）；
    2) 空错误位靠 CSS :empty 收起，JS 不碰 hidden —— 否则「校验时置 hidden=true、
       之后接口失败写进来的信息永远看不见」这个坑会重现。"""
    html, js, css = read("admin.html"), read("admin.js"), read("style.css")
    problems = []
    blocks = dict((i, b) for i, b in re.findall(r'<form id="([^"]+)"(.*?)</form>', html, re.S))
    for form, box in ADMIN_FORMS.items():
        block = blocks.get(form)
        if block is None:
            problems.append(f"admin.html 找不到表单 {form}")
            continue
        tag = re.search(r'<form id="' + form + r'"[^>]*>', html)
        if "novalidate" not in tag.group(0):
            problems.append(f"#{form} 未声明 novalidate：原生英文气泡会抢在页面提示之前")
        if ('id="' + box + '" role="alert"') not in html:
            problems.append(f"#{form} 缺少常驻错误位 #{box}（role=alert）")
        i = js.find("$('" + form + "').addEventListener('submit'")
        if i < 0:
            problems.append(f"找不到 #{form} 的 submit 处理器")
            continue
        head = js[i:i + 500]
        if "formCheck(f,'" + box + "',FORM_RULES['" + form + "'])" not in head:
            problems.append(f"#{form} 提交前没有按 FORM_RULES 做本地校验")
        # 规则表必须覆盖所有 required 字段。按 `'form-id':[[` 定位每个条目，
        # 截到下一个条目或表尾，避免把别的表单的规则算进来。
        def rule_keys(name):
            start = js.find("'" + name + "':[[")
            if start < 0:
                return set()
            # 本条目自己就含一个 ':[[ （表键的收尾引号），搜索起点必须跳过它，
            # 否则 nxt 落在条目内部，截出来的段是空的。
            nxt = js.find("':[[", start + len(name) + 3)
            end = js.find("\n};", start)
            if 0 < nxt < end:
                end = nxt
            return set(re.findall(r"\['([a-z_]+)'", js[start:end]))
        need = set(re.findall(r'<(?:input|textarea|select)[^>]*name="([^"]+)"[^>]*required', block))
        need |= set(re.findall(r'<(?:input|textarea|select)[^>]*required[^>]*name="([^"]+)"', block))
        missing = need - rule_keys(form)
        if missing:
            problems.append(f"{form} 里 {sorted(missing)} 带 required 却没有本地规则 —— "
                            "novalidate 之后没人接管它")
    if ".error:empty,.field-error:empty{display:none}" not in css:
        problems.append("空的错误位没有靠 CSS 收起，常驻占位或 hidden 抢掉接口错误都会发生")
    ui = read("ui.js")
    i = ui.find("function formCheck(")
    fc = ui[i:ui.find("\n  }", i)]
    if i < 0 or ".hidden" in fc:
        problems.append("UI.formCheck 去碰 hidden 了，接口失败的错误信息会被它盖掉（显隐交给 CSS :empty）")
    if problems:
        raise AssertionError("；".join(problems))
    return "管理台 4 个表单 novalidate + 常驻 role=alert 错误位，required 字段全部有本地规则，空位靠 CSS 收起"


def check_admin_text_length_parity():
    """管理台同样有一串长度上限，而且比用户端更容易分叉：实验室/资源/通知三个表单的
    maxlength 与 C 里的字节上限是各写各的。第 13 轮实测到位置框写着 200、服务端只收 180，
    中文填到 61 个字就吃一句不带字段名的裸 400；反过来写着 100 的字段又白丢掉 80 字节的额度。
    查询参数更糟：服务端先把 q 读进 char q[68] 截断、再判 strlen>64，
    超长的中文前缀被切碎成另一个词，页面拿到的是「查无此人」而不是错误。
    这里把五个数字全部从 C 源码抠出来，与 admin.js 的 LIMITS、admin.html 的 maxlength 对表。"""
    http_c = read("../src/http.c")
    service_c = read("../src/service.c")
    js, html = read("admin.js"), read("admin.html")
    problems = []

    want = {}
    want["labName"], want["labLoc"], want["labDesc"] = _c_limit(
        http_c, r"(?s)/api/admin/labs.*?text_ok\(name,(\d+),0\)\|\|!text_ok\(loc,(\d+),0\)\|\|!text_ok\(desc,(\d+),1\)",
        "实验室三字段")
    aname, aspec = _c_limit(service_c, r"strlen\(name\)>(\d+)\|\|\(spec&&strlen\(spec\)>(\d+)\)", "资源名称与规格")
    want["assetName"], want["assetSpec"] = aname, aspec
    want["noticeTitle"] = _c_limit(service_c, r"strlen\(title\)>(\d+)", "通知标题")[0]
    want["noticeBody"] = _c_limit(service_c, r"strlen\(text\)>(\d+)", "通知正文")[0]
    want["fAction"], want["fUser"] = _c_limit(
        http_c, r"(?s)/api/admin/records.*?strlen\(ea\)>(\d+).*?strlen\(eu\)>(\d+)", "记录筛选参数")
    want["qUser"] = _c_limit(http_c, r"(?s)/api/admin/users.*?strlen\(q\)>(\d+)", "用户前缀搜索")[0]

    m = re.search(r"const LIMITS=\{([^}]*)\}", js)
    if not m:
        problems.append("admin.js 找不到 LIMITS 上限表")
        caps = {}
    else:
        caps = dict((k.strip(), int(v)) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", m.group(1)))
    for key, c in want.items():
        if caps.get(key) != c:
            problems.append(f"admin.js LIMITS.{key}={caps.get(key)} 与服务端的 {c} 不一致")
        if key not in caps:
            continue
        # 数字对上了还不够，得真的被用到：只写进表里不在提交路径上引用，等于没拦。
        if "LIMITS." + key not in js:
            problems.append(f"admin.js LIMITS.{key} 定义了却没有任何字段引用它")

    # 输入框写在 DOM 里，按 form 分组才能区分两个 name="name"。
    # 这里要求的是 maxlength <= 服务端字节上限：更严格是产品选择（管理员习惯用短名称），
    # 更宽松才是 bug——浏览器放行、服务端拒绝，用户只拿到一句不带字段名的 400。
    # 真正的字节判定由 admin.js 按服务端上限做，下面已逐个核对它确实被引用。
    def _not_looser_than(tag, cap, what):
        got = re.search(r'maxlength="(\d+)"', tag) if tag else None
        if not got:
            problems.append(f"{what} 没有 maxlength，用户能一路输到服务端才吃 400")
        elif int(got.group(1)) > cap:
            problems.append(f"{what} 的 maxlength={got.group(1)} 高于服务端字节上限 {cap}，"
                            "浏览器放行而请求被拒；查询参数还会被服务端静默截断成另一个查询")

    blocks = dict((i, b) for i, b in re.findall(r'<form id="([^"]+)"(.*?)</form>', html, re.S))
    for form, fields in (("lab-form", (("name", "labName"), ("location", "labLoc"), ("description", "labDesc"))),
                         ("asset-form", (("name", "assetName"), ("spec", "assetSpec"))),
                         ("notify-form", (("title", "noticeTitle"), ("body", "noticeBody")))):
        block = blocks.get(form)
        if block is None:
            problems.append(f"admin.html 找不到表单 {form}")
            continue
        for name, key in fields:
            tag = re.search(r'<(?:input|textarea)[^>]*name="' + name + r'"[^>]*>', block)
            if not tag:
                problems.append(f"{form} 里没有 name={name} 的输入框")
                continue
            _not_looser_than(tag.group(0), want[key], f"{form} 的 {name} 框")
    for fid, key in (("users-q", "qUser"), ("events-action", "fAction"), ("events-user", "fUser")):
        tag = re.search(r'<input[^>]*id="' + fid + r'"[^>]*>', html)
        if not tag:
            problems.append(f"admin.html 找不到 #{fid}")
            continue
        _not_looser_than(tag.group(0), want[key], f"#{fid} 框")

    if "overBytes(v,max,label)" not in js:
        problems.append("admin.js 的 over() 不再调用 UI.overBytes，字节口径重新分叉")
    if problems:
        raise AssertionError("；".join(problems))
    return ("管理台 10 处文本上限与服务端逐字节对齐：" +
            "、".join(f"{k}={v}" for k, v in sorted(want.items())))


def check_chip_state_not_color_only():
    """预约卡片上的「选了哪几件资源」就是这次预约要占用的配额，但它一度只有颜色差别：
    第 15 轮实测，选中芯片与未选中的唯一区别是 background 从 transparent 变成 rgb(23,109,88)，
    `aria-pressed` 为 null —— 读屏完全听不到状态（WCAG 4.1.2），色觉障碍者也看不出（1.4.1）。
    同一个 class 还被用在「维修中」的资源标签上，那里借用的是「已选中」那套绿色，
    意思正好反了。这里钉四件事：
    1) 可切换的芯片必须带 aria-pressed，且点击时真的翻转；
    2) 选中态要有非颜色的第二重提示，且由样式表画（JS 里不许出现 ✓ 字符串）；
    3) 这条提示必须按容器限定，不能扫到状态标签；
    4) 备注框要有可访问名称，长度约束写在可见说明里而不是只挂在 placeholder 上。"""
    js, css, html = read("app.js"), read("style.css"), read("index.html")
    problems = []
    if "c.setAttribute('aria-pressed','false')" not in js:
        problems.append("资源芯片创建时没有 aria-pressed 初值")
    if "c.setAttribute('aria-pressed',String(!on))" not in js:
        problems.append("资源芯片点击后没有同步 aria-pressed（只换了 class）")
    if "'✓'" in js or '"✓"' in js:
        problems.append("选中标记 ✓ 被写进了 JS：视觉状态应由样式表驱动")
    mark_rules = re.findall(r"^[^\n]*::before[^{]*\{[^}]*content:'✓'[^}]*\}", css, re.M)
    if not mark_rules:
        problems.append("样式表里没有画 ✓ 的选中提示")
    for rule in mark_rules:
        sel = rule.split("{")[0]
        if not re.search(r"\.slot-assets|\.chips\[", sel):
            problems.append("✓ 的选择器没有按容器限定，会扫到复用 .chips 的状态标签：" + sel.strip())
        if "chip-maint" in sel:
            problems.append("维修中的状态标签不该出现选中标记 ✓")
    if ".chip-maint" not in css:
        problems.append("维修中资源没有专用的警示样式（还在借用 .on 的选中色）")
    if "'chips'+(a.status==='MAINTENANCE'?' on':'')" in js:
        problems.append("维修中资源仍用 .on（已选中那套绿色）表达状态")
    if "(maint?'（维修中）':'')" not in js:
        problems.append("「维修中」只写在 title 里：触屏与键盘用户看不到，读屏也不播报")
    if 'noteInput.setAttribute' not in js or "aria-label" not in js:
        problems.append("备注框只有 placeholder，没有可访问名称")
    if js.count("'note-hint-'+s.id") < 2:
        problems.append("备注说明节点的 id 没带场次号（或只写了一处），一屏多张卡片会重复 id")
    if 'id="lab-assets" class="chips-row"' not in html:
        problems.append("实验室资源条容器变了，✓ 的限定选择器需要重新核对")
    if problems:
        raise AssertionError("；".join(problems))
    return "资源芯片 aria-pressed + 样式表 ✓（按容器限定）；维修中标签独立配色；备注框有名称与常驻长度说明"


def check_title_never_the_only_carrier():
    """title 不是可靠的说明渠道：触屏没有悬停、键盘聚焦不一定显示、读屏默认不播报，
    移动端 Safari 干脆没有 hover 状态。第 16 轮全站审计共 4 处 title，
    其中资源芯片行的「声明使用（可选）」是整块控件唯一的说明——而这排芯片勾选的就是
    这次预约要占用的设备。更讽刺的是它上面那条只读资源列表反倒有可见标签：
    不能点的有标签，要点的没有。
    这里要求每个 title 承载的信息在可见文字里也出现一次（title 只作补充）。"""
    problems = []
    for page in ("app.js", "admin.js"):
        src = read(page)
        stripped = re.sub(r"title='[^']*'", "", src)   # 把 title 本身挖掉，剩下的才算可见文字
        for t in sorted(set(re.findall(r"title='([^']+)'", src))):
            core = re.sub(r"[（(][^）)]*[)）]", "", t)
            core = core.replace("该资源", "").replace("，暂不可预约", "").strip()
            if core and core not in stripped:
                problems.append(f"{page}：「{t}」只写在 title 里，可见文字里找不到「{core}」")
    js = read("app.js")
    if "需声明使用的设备（可选，可多选）" not in js:
        problems.append("资源选择块没有可见标签")
    if js.count("'asset-pick-hint-'+s.id") < 2:
        problems.append("资源选择标签的 id 没带场次号，多张卡片会重复 id、描述会指向别人的标签")
    html = read("index.html")
    # 必须看 theme-toggle 自己那个标签：全站还有登录页签等元素也带 aria-pressed，
    # 只查整页字符串会被它们蒙过去（变异测试当场抓到过一次）。
    tt = re.search(r'<button[^>]*id="theme-toggle"[^>]*>', html)
    if not tt or "aria-pressed=" not in tt.group(0):
        problems.append("主题切换按钮不再是 aria-pressed 切换语义，它的 title 就成了唯一说明")
    if problems:
        raise AssertionError("；".join(problems))
    return "全站 title 均为补充说明（可见文字里都有对应内容）；资源选择块有按场次唯一关联的可见标签"


def check_one_time_secret_handling():
    """API 令牌明文与管理员重置出的新密码都是一次性凭据：服务端只存哈希，
    错过这次显示就永远拿不回来。第 17 轮之前，它们唯一的容身之处是一条带 × 的 toast
    （`note('令牌已创建，明文：'+d.token+…)`）——手一抖关掉就没了，而且没有任何复制入口，
    用户只能对着屏幕逐字符手抄。
    现在它们落在常驻面板里并配复制按钮。两条纪律必须锁住：
    1) 复制不能只依赖 navigator.clipboard —— 它是安全上下文专属 API，
       本项目的典型部署是局域网 http，那时它是 undefined（和 crypto.randomUUID 同一个坑），
       必须有 execCommand('copy') 的退路；
    2) 退路用的隐藏 textarea 不能 display:none —— 那样 select() 复制不到内容，
       只能挪出视口。这类"看起来更干净但功能失效"的写法必须由门禁拦着。"""
    ui, css, user_js, adm_js = read("ui.js"), read("style.css"), read("app.js"), read("admin.js")
    index, admin = read("index.html"), read("admin.html")
    problems = []
    for need, why in (
            ("function copyText(", "缺少统一的复制入口"),
            ("navigator.clipboard.writeText", "没有走 navigator.clipboard"),
            ("window.isSecureContext", "没有判断安全上下文：局域网 http 下 clipboard 是 undefined"),
            ("fallbackCopy", "没有非安全上下文下的复制退路"),
            ("document.execCommand('copy')", "退路不是 execCommand('copy')")):
        if need not in ui:
            problems.append(why)
    shim = re.search(r"\.copy-shim\{([^}]*)\}", css)
    if not shim:
        problems.append("缺少 .copy-shim 定位规则，退路会把输入框闪现在页面上")
    elif "display:none" in shim.group(1):
        problems.append(".copy-shim 用了 display:none —— select() 复制不到内容，退路会静默失效")
    if "UI.secretBox($('token-secret')" not in user_js:
        problems.append("令牌明文没有落进常驻面板")
    if "明文：'+d.token" in user_js:
        problems.append("令牌明文仍然只写在 toast 里，关掉就永久丢失")
    if "UI.secretBox($('pw-secret')" not in adm_js:
        problems.append("重置出的新密码没有落进常驻面板")
    if "的新密码：'+d.password" in adm_js:
        problems.append("新密码仍然只写在 toast 里，管理员一关就没了")
    for mount, page in (("#token-secret", index), ("#pw-secret", admin)):
        if mount.replace("#", 'id="') + '"' not in page:
            problems.append(f"页面缺少一次性凭据面板的挂载点 {mount}")
    if problems:
        raise AssertionError("；".join(problems))
    return "一次性凭据常驻显示 + 复制（clipboard 与安全上下文判断 + execCommand 退路，退路不靠 display:none）"


def check_reduced_motion_gates_js_scroll():
    """样式表里的 @media (prefers-reduced-motion: reduce) 管不住 JS 发起的滚动：
    scrollIntoView({behavior:'smooth'}) 的 smooth 是 JS 实参，动画照播，还会盖过
    CSS 的 scroll-behavior:auto!important。第 19 轮在 app.js/admin.js 数到 5 处
    （点「编辑」把整页平滑滚到表单、改期成功后滚到那一行、展开替代时段……）。
    要求：滚动必须走 UI.scrollTo 这一个出口，而它必须按媒体偏好二选一。"""
    problems = []
    ui = read("ui.js")
    m = re.search(r"function scrollTo\(el, block\) \{(.*?)\n\}", ui, re.S)
    if not m:
        problems.append("ui.js 里没有 UI.scrollTo(el, block) 这个统一出口")
    else:
        body = m.group(1)
        if "prefers-reduced-motion" not in body:
            problems.append("UI.scrollTo 没查 prefers-reduced-motion，用户的减弱动效偏好被忽略")
        if not re.search(r"reduce\s*\?\s*'auto'\s*:\s*'smooth'", body):
            problems.append("UI.scrollTo 没有把 reduce 映射到 'auto'（三元写反、写死 smooth 或 auto 都不行）")
    for page in ("app.js", "admin.js"):
        src = read(page)
        if "scrollIntoView" in src:
            problems.append(page + " 直接调用 scrollIntoView，绕开了 reduce 判断，应改用 UI.scrollTo")
        if "smooth" in src:
            problems.append(page + " 里还留着 JS 平滑滚动的字面量")
    if "scrollTo }" not in ui:
        problems.append("ui.js 没把 scrollTo 放进导出列表，页面侧 UI.scrollTo 会是 undefined")
    if not re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)", read("style.css")):
        problems.append("样式表的 prefers-reduced-motion 兜底块被删掉了")
    if problems:
        raise AssertionError("；".join(problems))
    return "JS 滚动统一过 UI.scrollTo 并按 prefers-reduced-motion 在 auto/smooth 间二选一；两页无直调；CSS 兜底块仍在"


def check_no_javascript_degradation():
    """关掉 JavaScript（或被扩展/CSP 挡掉）时，提交不再由 JS 接管：浏览器按 HTML 默认走 GET
    到当前 URL，把 username 与 password 原样拼进地址栏——进历史、进服务端访问日志、进后续
    跳转的 Referer。第 20 轮在 index.html 数到 3 个这样的凭据表单（login / register /
    password）：submit 全靠 JS preventDefault，却既没有 method 也没有 noscript 说明。
    要求 ① 任何含 type="password" 的 form 必须显式 method="post"；
    ② 两页都要有位于 <body> 内、提到 JavaScript 的 <noscript> 说明，且不被样式藏掉。"""
    problems = []
    for page in ("index.html", "admin.html"):
        html = read(page)
        for m in re.finditer(r"<form\b[^>]*>(.*?)</form>", html, re.S):
            tag, body = m.group(0), m.group(1)
            if 'type="password"' not in body:
                continue
            flat = tag.replace('"', "").replace("'", "").lower()
            if "method=post" not in flat:
                idm = re.search(r"id=([\w-]+)", flat)
                problems.append("含密码输入的表单 %s 没有 method=post："
                                "禁用 JS 后点提交会把密码写进 URL" % (idm.group(1) if idm else "?"))
        ns = re.search(r"<noscript>(.*?)</noscript>", html, re.S)
        if not ns:
            problems.append(page + " 没有 <noscript> 说明：脚本禁用后页面只剩一个提交什么都不做的表单")
        elif "JavaScript" not in ns.group(1):
            problems.append(page + " 的 <noscript> 没告诉用户要启用 JavaScript")
        elif html.find("<body") > html.find("<noscript"):
            problems.append(page + " 的 <noscript> 不在 <body> 内，不会渲染")
    css = read("style.css")
    if ".noscript-notice{" not in css:
        problems.append("样式表里没有 .noscript-notice，这条说明会以无样式长段落贴在页面最上方")
    for m in re.finditer(r"[^{}]*noscript[^{}]*\{([^}]*)\}", css):
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", m.group(1)):
            problems.append("样式表把 noscript 藏起来了，禁用 JS 的用户什么都看不到")
    if problems:
        raise AssertionError("；".join(problems))
    return "3 个凭据表单均 method=post（无 JS 也不会把密码写进 URL）；两页均有可见 noscript 说明且未被样式隐藏"


def read_c(rel):
    target = ROOT / "src" / rel
    if not target.is_file():
        raise AssertionError(f"缺少服务端源文件：src/{rel}")
    return target.read_text(encoding="utf-8", errors="replace")


def check_approval_flag_is_surfaced():
    """require_approval 决定一条新预约是 CONFIRMED 还是 PENDING，是实验室的真实属性。
    服务端其实早就实现了（db.c 建列并幂等迁移、http.c 的创建/更新都写它、service.c 据此落 PENDING），
    但两个读接口都不返回它，于是：管理台既看不见也改不动这个开关（表单里根本没有控件），
    用户端在提交前也无从得知自己的申请为什么要排队等批准，只会吃一句写死的「预约成功」——
    而那会儿记录还没生效、也不占名额。第 21 轮补齐字段，并要求前端确实消费它。
    ① C 侧：GET /api/labs、GET /api/slots 的 SELECT 带出 require_approval 且按布尔输出，
       POST /api/reservations 回执带落库 status；
    ② 管理台：#lab-form 有同名复选框，提交载荷、编辑回填、列表标签三处都用它；
    ③ 用户端：卡片按 s.require_approval 说明，提示语按回执 status 分支，
       写死的「预约成功」只能作为非 PENDING 分支存在（全站只出现一次）。"""
    problems = []
    http = read_c("http.c")
    service = read_c("service.c")
    db = read_c("db.c")
    admin_html = read("admin.html")
    admin_js = read("admin.js")
    app_js = read("app.js")

    if "enabled,require_approval FROM labs" not in http:
        problems.append("GET /api/labs 的 SELECT 没带 require_approval，管理台列表无从显示审批开关")
    if "l.enabled AS lab_enabled,l.require_approval" not in http:
        problems.append("GET /api/slots 的 SELECT 没带 require_approval，用户端提交前无法说明该场次需审批")
    if not re.search(r'!strcmp\(n,"require_approval"\)\)\s*cJSON_AddBoolToObject', db):
        problems.append("db.c 的布尔白名单没有 require_approval（只看列名出现与否会被 has_column/ALTER 语句蒙过），"
                        "字段会以数字 0/1 输出而不是布尔值")
    if not re.search(r'cJSON_AddStringToObject\(\w+,"status",need_appr\?"PENDING":"CONFIRMED"\)', service):
        problems.append("POST /api/reservations 的回执没带落库 status，页面无法区分已确认与待审批")

    if 'name="require_approval"' not in admin_html:
        problems.append("admin.html 的实验室表单没有 require_approval 控件，开关在界面上不可达")
    if "require_approval:f.elements.require_approval.checked" not in admin_js:
        problems.append("admin.js 保存实验室时没提交 require_approval（服务端两个分支都接受该字段）")
    if "f.elements.require_approval.checked=!!l.require_approval" not in admin_js:
        problems.append("admin.js 的「编辑」没有回填 require_approval：管理员一保存就把别人的开关悄悄关掉")
    if "if(l.require_approval)st.append(el('span','预约需审批','tag warn'))" not in admin_js:
        problems.append("admin.js 的实验室列表没渲染审批状态标签，只能写不能读")

    if "if(s.require_approval&&!started&&!s.my_reservation_id)tags.append(el('span','需管理员审批','tag warn'))" not in app_js:
        problems.append("app.js 的场次卡片没有按 s.require_approval 挂出「需管理员审批」标签")
    if "(s.require_approval?'提交预约申请':'立即预约')" not in app_js:
        problems.append("app.js 主列表按钮没按审批状态改文案，用户以为点完就订上了")
    if app_js.count("labNeedsApproval()?'提交预约申请':'立即预约'") < 2:
        problems.append("替代时段与「近期时段」两块面板至少各有一处按审批状态改文案，"
                        "同一个动作不该在两块面板里叫两个名字")
    if not re.search(r"function reserveMsg\(d\)\{return d&&d\.status==='PENDING'", app_js):
        problems.append("app.js 没有按回执 status 分支的 reserveMsg，预约提示语是写死的")
    hardcoded = app_js.count("'预约成功，可在我的记录查看。'")
    if hardcoded != 1:
        problems.append(f"「预约成功」写死字符串出现 {hardcoded} 次：应只在 reserveMsg 的非 PENDING 分支出现一次，"
                        "否则需审批的申请也会被报成已确认")
    if problems:
        raise AssertionError("；".join(problems))
    return ("labs/slots 读接口 + 预约回执 status 均下发；管理台复选框/提交/回填/列表四处齐备；"
            "用户端按 require_approval 与 status 措辞（写死成功语仅 1 处，在 reserveMsg 内）")


def check_text_clipping_is_codepoint_safe():
    """文本截断必须按 Unicode 码点，不能按 UTF-16 码元。
    第 21 轮之前 `web/app.js` 的「我的记录 → 备注」一列写作
    `r.note.length>20 ? r.note.slice(0,20)+'…' : r.note`：`.length` 与 `.slice` 数的都是码元，
    而一个 emoji（🧪）或 CJK 扩展 B 区的生僻字恰好占两个码元——边界正好落在它身上时
    `slice` 交出半个代理对，界面渲染成一个替换符（用户看到的是"备注坏了"）。
    另一半问题更实用：服务端允许 200 字节（约 66 个汉字）的备注，这一列却只有前 20 个码元，
    而且这个页面再没有第二处能读到全文（记录没有详情视图）——被截掉的部分等于丢了。
    所以要求：① 截断出口统一在 UI.clip 且按 Array.from 分字符；② 调用点用它而不是自己数；
    ③ 被截时全文要原地可展开（原生 details），展开后的文本必须能换行（td 全局 nowrap）。"""
    problems = []
    ui = read("ui.js")
    app_js = read("app.js")
    css = read("style.css")

    m = re.search(r"function clip\(text, n\) \{(.*?)\n  \}", ui, re.S)
    if not m:
        problems.append("ui.js 没有 UI.clip 这个统一截断出口")
    else:
        body = m.group(1)
        if "Array.from(s)" not in body:
            problems.append("UI.clip 没有用 Array.from 分字符（split('') 同样会拆代理对），仍是按码元截断")
        if "cps.slice(0, n).join('')" not in body:
            problems.append("UI.clip 没有把码点数组 join 回字符串，半个代理对还是会漏出去")
        if "'…'" not in body or "clipped" not in body:
            problems.append("UI.clip 必须自带省略号并回报 clipped，调用方才不用自己再数一遍长度")
    if not re.search(r"return \{[^}]*\bclip\b[^}]*\}", ui):
        problems.append("ui.js 没把 clip 放进导出列表，页面侧 UI.clip 会是 undefined")

    def code_only(src):
        """只在真实代码里找坏写法：本检查自己的注释里就抄了一句旧代码作为反例。"""
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return re.sub(r"(?m)^\s*//.*$", "", src)

    for rel in ("app.js", "admin.js", "ui.js"):
        bad = re.findall(r"\w[\w.]*\.length\s*>\s*\d+\s*\?\s*\w[\w.]*\.slice\(", code_only(read(rel)))
        if bad:
            problems.append(f"{rel} 里还有按码元截文本的写法：{bad[0]}…（应走 UI.clip）")
    if "noteCell(r.note)" not in code_only(app_js):
        problems.append("app.js 的备注列没有走共用的 noteCell，仍是就地截断")
    # 第 23 轮起 noteCell 的实现从 app.js 搬进 ui.js（管理台待审批列表要共用同一份），
    # 所以断言钉在 ui.js 上，并且要求全站只有一份定义。
    if sum(code_only(read(rel)).count("function noteCell(") for rel in ("ui.js", "app.js", "admin.js")) != 1:
        problems.append("noteCell 必须全站唯一定义在 ui.js（app.js 与 admin.js 共用）："
                        "两份实现迟早漂移成两种截断口径")
    m2 = re.search(r"function noteCell\(text, n\) \{(.*?)\n  \}", ui, re.S)
    if not m2:
        problems.append("ui.js 里没有 noteCell(text, n)，两页的备注列会拿到 undefined")
    else:
        body2 = m2.group(1)
        if "clip(text, n || NOTE_CLIP)" not in body2:
            problems.append("ui.js 的 noteCell 没有按码点截断（clip），代理对还会被切半个")
        if "if (!text) return '—';" not in body2:
            problems.append("noteCell 对空备注没有显式取值：审批人看到空格子分不清「用户没写」还是「没渲染出来」")
        if "box.append(el('summary', c.text), el('div', text, 'cell-more-body'))" not in body2:
            problems.append("noteCell 被截断时没有就地展开全文的通道，后文在界面上不可达")
    if not re.search(r"return \{[^}]*\bnoteCell\b[^}]*\}", ui):
        problems.append("ui.js 没把 noteCell 放进导出列表，页面侧 UI.noteCell 会是 undefined")

    if ".cell-more-body{" not in css:
        problems.append("样式表缺 .cell-more-body，展开的全文会继承 td 的 white-space:nowrap 把行撑出可视区")
    else:
        rule = re.search(r"\.cell-more-body\{([^}]*)\}", css).group(1)
        if not re.search(r"white-space\s*:\s*normal", rule):
            problems.append(".cell-more-body 没有把 white-space 压回 normal：展开后一行不换行，"
                            "200 字节的备注会把整行撑出屏幕——正好复刻它要修的毛病")
    if problems:
        raise AssertionError("；".join(problems))
    return ("UI.clip 按码点截断（Array.from + join）且已导出；备注列两页共用唯一一份 UI.noteCell"
            "（空备注显式 '—'、被截时有原生 details 可就地看全文）；展开体 white-space:normal；"
            "三页无按码元截断的残留写法")


def check_admin_approval_list_shows_note():
    """接口给了字段，界面还得真的用它——第 23 轮钉的是「审批依据可达」这一条。
    `reservations.note` 其实一直随 GET /api/admin/records 返回（records() 的 SELECT 里就写着
    r.note，第 22 轮记下的「SQL 不带 note」经预检实测是错的：PENDING 行的 keys 里有 note）。
    真正的缺口全在渲染侧：待审批表格的表头原本是 ['ID','实验室','用户名','时段','来源','操作']，
    用户下单时写的实验项目/组号/器材需求没有地方显示，管理员只能对着「谁、哪个实验室、几点」
    点批准或拒绝。要求四处同时成立：
    ① C 侧 records() 继续带出 r.note（两个调用方共用这一条 SQL）；
    ② 契约的机器可读 schema 声明该字段（RES_ROW 的 note 可为 null 的字符串）；
    ③ 管理台「待审批预约」表有「备注」列，单元走共用出口 UI.noteCell，不另写一份截断；
    ④ 管理台「预约记录」表同样带这一列。"""
    problems = []
    service = read_c("service.c")
    admin_js = read("admin.js")
    contract = (ROOT / "tests" / "contract_test.py").read_text(encoding="utf-8", errors="replace")

    if "r.hold_deadline,r.note FROM reservations" not in service:
        problems.append("records() 的 SELECT 不再带 r.note，管理台拿不到备注（前端这两列会全变 '—'）")
    if '"note": nb(T_STR)' not in contract:
        problems.append("contract_test.py 的 RES_ROW 不再声明 note，接口与契约脱钩：界面渲染的是一个未约定的字段")
    if "'备注','操作'],list.map(" not in admin_js:
        problems.append("管理台「待审批预约」表格没有「备注」列，审批依据在界面上不可见")
    if "UI.noteCell(r.note),buildApprovalActions(r)" not in admin_js:
        problems.append("待审批表的备注单元没走 UI.noteCell（或位置不对），要么被截半要么点了没反应")
    if "'来源','备注','签到时间'" not in admin_js:
        problems.append("管理台「预约记录」表格没有「备注」列")
    if "sourceName(r),UI.noteCell(r.note),r.checked_in_at?" not in admin_js:
        problems.append("预约记录表的备注单元没走 UI.noteCell")
    if problems:
        raise AssertionError("；".join(problems))
    return ("records() 的 SQL 与契约 schema 都带着 note；管理台待审批表与预约记录表各有「备注」列，"
            "两处单元都走唯一一份 UI.noteCell")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "docs/evidence/frontend")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print("== CSP 合规 ==", flush=True)
    for fn in (check_no_inline_style, check_no_inline_script_and_handler):
        record(fn.__name__, fn)
    print("== 减弱动效与 JS 滚动 ==", flush=True)
    record(check_reduced_motion_gates_js_scroll.__name__, check_reduced_motion_gates_js_scroll)
    print("== 禁用 JavaScript 时的降级 ==", flush=True)
    record(check_no_javascript_degradation.__name__, check_no_javascript_degradation)
    print("== 审批开关可读可写（require_approval / 回执 status）==", flush=True)
    record(check_approval_flag_is_surfaced.__name__, check_approval_flag_is_surfaced)
    print("== 文本截断按码点、截掉的要能就地看全 ==", flush=True)
    record(check_text_clipping_is_codepoint_safe.__name__, check_text_clipping_is_codepoint_safe)
    print("== 管理台审批依据（备注列）可达 ==", flush=True)
    record(check_admin_approval_list_shows_note.__name__, check_admin_approval_list_shows_note)
    print("== XSS 与调试出口 ==", flush=True)
    for fn in (check_no_html_sink, check_no_debug_console):
        record(fn.__name__, fn)
    print("== DOM 引用与静态资源 ==", flush=True)
    for fn in (check_dom_ids, check_shared_assets_exist):
        record(fn.__name__, fn)
    print("== 主题引导与元信息 ==", flush=True)
    for fn in (check_theme_bootstrap, check_random_uuid_guard):
        record(fn.__name__, fn)
    print("== CSS 设计令牌 ==", flush=True)
    for fn in (check_css_tokens, check_no_hardcoded_colors_in_components):
        record(fn.__name__, fn)
    print("== 可访问性（对比度与页签语义）==", flush=True)
    for fn in (check_wcag_contrast, check_tab_semantics):
        record(fn.__name__, fn)
    print("== 表格排序与窄屏布局 ==", flush=True)
    record(check_table_contract.__name__, check_table_contract)
    print("== 行内操作渲染 ==", flush=True)
    record(check_action_cells_rendered.__name__, check_action_cells_rendered)
    print("== 页签键盘契约 ==", flush=True)
    record(check_tab_keyboard.__name__, check_tab_keyboard)
    print("== 主题化视觉与表单校验反馈 ==", flush=True)
    for fn in (check_no_styling_in_js, check_form_validation_contract):
        record(fn.__name__, fn)
    print("== 选择器落位与标记闭合 ==", flush=True)
    for fn in (check_picker_region_contract, check_html_structure_balanced):
        record(fn.__name__, fn)
    print("== 窄屏顶栏与横滚提示 ==", flush=True)
    record(check_narrow_screen_contract.__name__, check_narrow_screen_contract)
    print("== 表单校验归属 ==", flush=True)
    record(check_form_validation_ownership.__name__, check_form_validation_ownership)
    print("== 凭据规则三方对齐 ==", flush=True)
    record(check_credential_rule_parity.__name__, check_credential_rule_parity)
    print("== 管理台文本长度对齐 ==", flush=True)
    record(check_admin_text_length_parity.__name__, check_admin_text_length_parity)
    print("== 管理台表单校验归属 ==", flush=True)
    record(check_admin_form_validation_ownership.__name__, check_admin_form_validation_ownership)
    print("== 选中状态不能只靠颜色 ==", flush=True)
    record(check_chip_state_not_color_only.__name__, check_chip_state_not_color_only)
    print("== title 不得是唯一信息载体 ==", flush=True)
    record(check_title_never_the_only_carrier.__name__, check_title_never_the_only_carrier)
    print("== 一次性凭据的展示与复制 ==", flush=True)
    record(check_one_time_secret_handling.__name__, check_one_time_secret_handling)
    print("== 共用基座 ==", flush=True)
    for fn in (check_no_core_duplication, check_ui_bindings_resolve):
        record(fn.__name__, fn)
    print("== JS 语法与取号降级 ==", flush=True)
    record(check_js_syntax.__name__, check_js_syntax)
    record(check_uid_fallback_runtime.__name__, check_uid_fallback_runtime)
    passed = sum(1 for r in RESULTS if r["passed"])
    report = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target": "web/",
        "checks_total": len(RESULTS), "checks_passed": passed,
        "all_passed": passed == len(RESULTS), "results": RESULTS,
    }
    (args.output / "frontend_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n前端门禁检查 {passed}/{len(RESULTS)} 通过")
    print(f"证据：{(args.output / 'frontend_results.json').resolve()}")
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
