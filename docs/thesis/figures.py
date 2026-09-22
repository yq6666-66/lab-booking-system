# -*- coding: utf-8 -*-
"""论文插图生成：6 张示意图（架构/ER/状态转换/时序×2/去重流程），输出 PNG。

排版纪律（2026-09-14 重排）：
- box()/ent() 内置适配校验：按字号估算文本行宽（中文全宽、ASCII 0.58 宽）与行数×行高，
  超出框内可用尺寸即打印 FIT-WARN，脚本要求零警告；
- 标签一律显式指定位置，不再默认取箭头中点，避免平行箭头标签互压。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
import pathlib

plt.rcParams["font.family"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False
OUT = pathlib.Path(__file__).resolve().parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)
G = "#176d58"; GB = "#e7eee8"; BD = "#5a7a6e"; TX = "#18322d"

WARN = []
CHECKS = []   # (图名, 框patch, 文本artist) 文本必须落在框内
FREES = []    # (图名, 自由文本artist) 两两之间、与所有框之间不得相交

def _renderer_check(fig, name):
    """渲染后用真实像素包围盒复核：框内文本不越框，自由标签互不压盖。"""
    fig.canvas.draw()
    ren = fig.canvas.get_renderer()
    def bb(a): return a.get_window_extent(ren)
    for cname, patch, text in CHECKS:
        if patch.figure is not fig: continue
        pb, tb = bb(patch), bb(text)
        if tb.x0 < pb.x0 - 2 or tb.x1 > pb.x1 + 2 or tb.y0 < pb.y0 - 2 or tb.y1 > pb.y1 + 2:
            WARN.append(f"{cname}[{name}]: 文本越框（实测 bbox）")
    frees = [(c, t) for c, t in FREES if t.figure is fig]
    boxes = [(c, p) for c, p, _ in CHECKS if p.figure is fig]
    for i in range(len(frees)):
        for j in range(i + 1, len(frees)):
            if bb(frees[i][1]).overlaps(bb(frees[j][1])):
                WARN.append(f"{name}: 自由标签重叠 {frees[i][0]} × {frees[j][0]}")
    for c, t in frees:
        for bc, p in boxes:
            if bb(t).overlaps(bb(p)):
                WARN.append(f"{name}: 标签 '{c}' 压到框 {bc}")

def _text_w_units(text, fs, unit_w_inch):
    """估算一行文本在数据坐标系中的宽度（中文≈fs/72 英寸，ASCII≈0.58×fs/72）。"""
    w_in = sum((fs / 72.0 if ord(ch) > 127 else 0.58 * fs / 72.0) for ch in text)
    return w_in / unit_w_inch

def _fit_check(name, text, fs, w_units, h_units, unit_w_inch, unit_h_inch, pad=0.12):
    lines = text.split("\n")
    line_h_in = fs / 72.0 * 1.25
    for ln in lines:
        tw = _text_w_units(ln, fs, unit_w_inch)
        if tw > w_units - pad:
            WARN.append(f"{name}: 行宽溢出 '{ln[:18]}…' 需 {tw:.2f}u > 框 {w_units - pad:.2f}u (fs={fs})")
    need_h = len(lines) * line_h_in / unit_h_inch
    if need_h > h_units + 0.05:
        WARN.append(f"{name}: 行数溢出 {len(lines)} 行需 {need_h:.2f}u > 框 {h_units:.2f}u (fs={fs})")

def box(ax, x, y, w, h, text, fc=GB, ec=BD, fs=10, bold=False, name="box"):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02", fc=fc, ec=ec, lw=1.2)
    ax.add_patch(p)
    t = ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                color=TX, fontweight="bold" if bold else "normal", linespacing=1.35)
    CHECKS.append((name, p, t))
    _fit_check(name, text, fs, w, h, ax.unit_w, ax.unit_h)

def arrow(ax, x1, y1, x2, y2, text="", fs=8.5, ls="-", lx=None, ly=None, ha="center"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14,
                                 color=BD, lw=1.2, linestyle=ls))
    if text:
        t = ax.text((x1 + x2) / 2 if lx is None else lx, (y1 + y2) / 2 + 0.015 if ly is None else ly,
                    text, ha=ha, fontsize=fs, color=BD)
        FREES.append((f"箭头标签:{text[:8]}", t))

def note(ax, x, y, text, tag=None, **kw):
    """自由文本（说明/标签/列头），参与渲染级重叠复核。"""
    t = ax.text(x, y, text, **kw)
    FREES.append((f"注:{(tag or text)[:10]}", t))
    return t

def canvas(w=10, h=6.5, xr=10, yr=10):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, xr); ax.set_ylim(0, yr); ax.axis("off")
    ax.unit_w = w / xr; ax.unit_h = h / yr  # 每数据单位的英寸数
    return fig, ax

def save(fig, name):
    _renderer_check(fig, name)
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig); print("saved", name)

# ---- 图3-1 系统用例图（学生 / 管理员双角色，系统边界内两列用例） ----
fig, ax = canvas(10, 7.2)
ax.add_patch(Rectangle((1.9, 0.35), 6.5, 6.5, fc="white", ec=BD, lw=1.6))
note(ax, 5.15, 6.55, "实验室资源管理与预约候补一体化系统", ha="center", fontsize=10.5, fontweight="bold", color=G)
box(ax, 0.15, 3.3, 1.35, 1.0, "学生\n（普通用户）", fc="#dbe9e2", bold=True, fs=9.5, name="3-1actorS")
box(ax, 8.6, 3.3, 1.3, 1.0, "管理员", fc="#dbe9e2", bold=True, fs=9.5, name="3-1actorA")
left_uc = ["注册 / 登录 / 改密", "查询场次与资源", "预约 / 改期 / 取消", "加入候补 / 退出",
           "签到 / 签退", "查看记录与通知", "管理 API 令牌"]
right_uc = ["发布 / 调整场次", "预约审批（含批量）", "用户与凭据管理", "强制取消 / 代签退",
            "资源维护 / 资格授权", "统计 / 导出 / 监控", "通知发布 / 公告"]
for i, t in enumerate(left_uc):
    y = 5.5 - i * 0.75
    box(ax, 2.15, y, 2.9, 0.6, t, fc="#f4f8f6", fs=8.5, name=f"3-1L{i}")
    ax.plot([1.5, 2.15], [3.8, y + 0.3], color=BD, lw=0.9)
for i, t in enumerate(right_uc):
    y = 5.5 - i * 0.75
    box(ax, 5.35, y, 2.9, 0.6, t, fc="#f4f8f6", fs=8.5, name=f"3-1R{i}")
    ax.plot([8.25, 8.6], [y + 0.3, 3.8], color=BD, lw=0.9)
note(ax, 5.15, 0.62, "管理员经用户入口亦可执行学生侧全部用例（role_hint 双入口，正文 5.2）", ha="center", fontsize=8.6, color=BD)
save(fig, "fig3-1-usecase.png")

# ---- 图4-1 系统总体架构（重排：子框两行化、横幅拆行、层距加大） ----
fig, ax = canvas(10, 7.2)
box(ax, 0.5, 8.75, 9, 0.95, "浏览器（原生 HTML/JS/CSS：用户端 + 管理控制台）", fc="#dbe9e2", bold=True, fs=11, name="4-1浏览器")
note(ax, 5, 8.45, "JSON over HTTP（仅 127.0.0.1 回环）", ha="center", fontsize=9, color=BD)
ax.add_patch(FancyArrowPatch((5, 8.72), (5, 8.22), arrowstyle="-|>", mutation_scale=14, color=BD))
box(ax, 0.5, 6.55, 9, 1.45, "", fc=GB, ec=BD)
ax.text(5, 7.78, "CivetWeb 接入层（8 工作线程 · 回环监听）", ha="center", fontsize=11, fontweight="bold", color=TX)
for i, t in enumerate(["会话认证", "CSRF / Origin\n+ Host 校验", "请求体\n≤ 16 KB", "安全响应头\n五项（CSP 等）"]):
    box(ax, 0.75 + i * 2.2, 6.7, 2.0, 0.62, t, fc="white", fs=8.5, name=f"4-1接入{i}")
box(ax, 0.5, 4.15, 9, 1.5, "", fc=GB, ec=BD)
ax.text(5, 5.38, "业务层（BEGIN IMMEDIATE 单写者事务 · 五模块）", ha="center", fontsize=11, fontweight="bold", color=TX)
for i, t in enumerate(["booking\n预约候补审批", "asset\n资源维护资格", "stats / token\n统计·令牌信用", "service\n通知会话扫描", "http / db\n接入与数据访问"]):
    box(ax, 0.62 + i * 1.82, 4.32, 1.72, 0.68, t, fc="white", fs=7.8, name=f"4-1业务{i}")
box(ax, 6.9, 2.55, 2.6, 0.85, "限流防爆破\n令牌桶 + 登录锁定", fc="#f6efdf", fs=8.5, name="4-1限流")
box(ax, 6.9, 1.45, 2.6, 0.85, "运行指标\n原子计数 + 延迟直方图", fc="#f6efdf", fs=8.5, name="4-1指标")
box(ax, 0.5, 2.55, 6.0, 0.85, "结构化日志（分级 · 轮转 · 访问日志 · 慢请求告警）", fc="#f6efdf", fs=9, name="4-1日志")
box(ax, 0.5, 1.2, 5.9, 1.0, "数据访问 db.c\n绑定 · 语句缓存\n连接复用 · 幂等迁移", bold=True, fs=9, name="4-1db")
box(ax, 0.5, 0.1, 9, 0.85, "SQLite（WAL · synchronous=FULL · 外键 · 备份 API）", fc="#dbe9e2", bold=True, fs=10, name="4-1sqlite")
for y in (8.22, 6.5, 4.05, 2.45, 1.12):
    ax.add_patch(FancyArrowPatch((5, y), (5, y - 0.22), arrowstyle="-|>", mutation_scale=12, color=BD))
save(fig, "fig4-1-architecture.png")

# ---- 图4-2 ER 图（重排：字段行距 0.09→0.26，画布放大，实体加宽） ----
fig, ax = canvas(12.5, 8, xr=12.5, yr=10)
def ent(x, y, w, name, fields):
    fh = 0.26  # 每字段行高（数据单位）
    hh = 0.44  # 表头高
    box(ax, x, y, w, hh, name, fc="#cfe3d8", bold=True, fs=9.5, name=f"ent-{name}")
    bh = fh * len(fields)
    ax.add_patch(Rectangle((x, y - fh * len(fields)), w, fh * len(fields), fc="white", ec=BD, lw=1))
    for i, f in enumerate(fields):
        ax.text(x + 0.09, y - fh / 2 - i * fh, f, fontsize=8.2, va="center", color=TX)
        # 字段行宽校验（左对齐文本，可用宽度 = w - 两侧留白）
        tw = _text_w_units(f, 8.2, ax.unit_w)
        if tw > w - 0.16:
            WARN.append(f"ent-{name}: 字段溢出 '{f}' 需 {tw:.2f}u > {w - 0.16:.2f}u")
def line(x1, y1, x2, y2, label="", lx=None, ly=None):
    ax.plot([x1, x2], [y1, y2], color=BD, lw=1)
    if label:
        note(ax, (x1 + x2) / 2 if lx is None else lx, (y1 + y2) / 2 + 0.1 if ly is None else ly,
             label, fontsize=8, color=BD, ha="center")
ent(0.2, 9.3, 2.3, "users", ["id PK", "username UQ", "password_hash", "role / enabled"])
ent(0.2, 5.6, 2.3, "sessions", ["token_hash PK", "user_id FK", "csrf_token", "expires_at"])
ent(3.0, 9.3, 2.3, "labs", ["id PK", "name UQ", "location", "enabled"])
ent(3.0, 5.6, 2.3, "slots", ["id PK", "lab_id FK", "start_at / end_at", "capacity（v3）", "reminded_at（v4）"])
ent(5.8, 9.3, 2.5, "reservations", ["id PK", "user_id FK", "slot_id FK", "status / source", "checked_in_at（v2）", "cancel_reason（v2）"])
ent(5.8, 4.6, 2.5, "waitlist", ["id PK", "user_id FK", "slot_id FK", "status（FIFO）", "promoted_id FK"])
ent(8.9, 9.3, 2.5, "request_receipts", ["user_id+req_id PK", "action / digest", "http_status", "result_json"])
ent(8.9, 5.8, 2.5, "operation_events", ["id PK", "actor_id FK", "action", "entity_id / req_id"])
ent(8.9, 2.6, 2.5, "notifications", ["id PK", "user_id FK", "kind（四类）", "read_at", "slot / rsv FK"])
ent(5.8, 1.6, 2.5, "assets（r13）", ["id PK", "lab_id FK", "name UQ per lab", "spec / total", "status（3 态）"])
ent(8.9, 0.35, 2.5, "asset_claims（r14）", ["rsv+asset PK", "reservation FK", "asset FK", "created_at"])
line(8.05, 2.4, 8.9, 1.3, "1:N", lx=9.2, ly=1.55)
line(4.2, 6.0, 5.8, 2.6, "1:N 配备", lx=4.9, ly=4.1)
line(2.5, 9.55, 3.0, 9.6, "1:N", lx=2.75, ly=9.87)
line(1.0, 9.3, 1.0, 6.6, "会话 1:N", lx=1.15, ly=8.0)
line(2.5, 9.3, 5.8, 9.55, "1:N", lx=4.0, ly=9.87)
line(2.5, 9.1, 5.8, 6.3, "候补 1:N", lx=3.6, ly=7.6)
line(4.2, 9.3, 6.4, 8.5, "占用 1:N", lx=5.1, ly=9.0)
line(4.2, 5.6, 6.4, 5.6, "1:N", lx=5.56, ly=5.42)
line(5.3, 5.6, 6.6, 5.4, "场次 1:N", lx=5.7, ly=5.15)
line(8.3, 9.0, 8.9, 9.0, "去重 N:1", lx=8.6, ly=9.18)
line(8.3, 8.6, 8.9, 6.3, "审计 N:1", lx=8.25, ly=7.4)
line(8.3, 8.2, 8.9, 3.6, "通知 N:1", lx=8.05, ly=5.6)
note(ax, 0.25, 1.15, "不变量：每场次占容（CONFIRMED+HELD）≤ capacity\n每(用户,场次)至多 1 条 WAITING；回执(用户,请求编号)幂等\nassets(lab_id,name) 唯一；schema v5 共 17 表\n上图为核心 11 表；r17–r24 幂等迁移扩展 6 表：\nasset_claims 资源声明 · credit_ledger 信用流水\noutbox 通知外发 · api_tokens 令牌（仅存哈希）\nasset_windows 时段窗 · asset_maintenance 维护工单\nqualifications 资格 · labs.require_approval 开关\nreservations 增 PENDING/HELD/EXPIRED\n新增 hold_deadline、note、checked_out_at 列",
     fontsize=8.2, color=G, ha="left", linespacing=1.5)
save(fig, "fig4-2-er.png")

# ---- 图4-3 状态转换图（预约五态：PENDING/CONFIRMED/HELD/CANCELLED/EXPIRED + 候补四态：WAITING/PROMOTED/WITHDRAWN/SKIPPED） ----
fig, ax = canvas(10.5, 6.4, xr=10.5, yr=10.6)
note(ax, 0.35, 10.15, "预约状态机（五态）", fontsize=10.5, fontweight="bold", color=G)
box(ax, 0.6, 8.6, 2.0, 0.9, "PENDING\n待审批（不占容）", fc="#fdeecd", bold=True, fs=9.5, name="4-3PEND")
box(ax, 4.0, 8.6, 2.3, 0.9, "CONFIRMED\n有效预约（占容）", fc="#cfe3d8", bold=True, fs=9.5, name="4-3CONF")
box(ax, 8.0, 8.6, 2.1, 0.9, "HELD\n限时保留（占容）", fc="#dce8f5", bold=True, fs=9.5, name="4-3HELD")
box(ax, 4.0, 6.1, 2.3, 0.9, "CANCELLED\n终态", fc="#f3e3e0", bold=True, fs=9.5, name="4-3CANC")
box(ax, 8.0, 6.1, 2.1, 0.9, "EXPIRED\n保留超时", fc="#efe6f3", bold=True, fs=9.5, name="4-3EXP")
arrow(ax, 2.6, 9.05, 4.0, 9.05)
note(ax, 3.3, 9.5, "approve\n（重校容量）", ha="center", fontsize=8.6, color=BD, linespacing=1.4)
arrow(ax, 1.6, 8.6, 4.4, 7.0)
note(ax, 2.3, 7.55, "reject\nreason=REJECTED", ha="center", fontsize=8.6, color=BD, linespacing=1.4)
arrow(ax, 6.3, 9.05, 8.0, 9.05)
note(ax, 7.15, 9.5, "取消释放\n→ 候补递补", ha="center", fontsize=8.6, color=BD, linespacing=1.4)
arrow(ax, 6.3, 8.6, 4.9, 7.0)
note(ax, 5.75, 7.7, "取消 USER / 爽约 NO_SHOW / 抢占 PREEMPTED", ha="center", fontsize=8.4, color=BD)
arrow(ax, 9.05, 8.6, 9.05, 7.0)
note(ax, 9.9, 7.8, "超时扫描\n(hold_deadline)", ha="center", fontsize=8.6, color=BD, linespacing=1.4)
note(ax, 0.35, 2.3, "取消原因五分：USER / NO_SHOW /\nREJECTED / PREEMPTED / ADMIN\nCONFIRMED 期间可签到—签退", fontsize=8.2, color=BD, ha="left", linespacing=1.4)
note(ax, 0.35, 4.75, "候补状态机（四态）", fontsize=10.5, fontweight="bold", color=G)
box(ax, 0.6, 3.4, 2.0, 0.9, "WAITING\n候补排队", fc="#fdf3d8", bold=True, fs=9.5, name="4-3WAIT")
box(ax, 4.0, 3.4, 2.3, 0.9, "PROMOTED\n已递补", fc="#dbe9e2", bold=True, fs=9.5, name="4-3PROM")
box(ax, 7.2, 3.4, 1.9, 0.9, "WITHDRAWN\n已退出", fc="white", fs=9.5, name="4-3WD")
arrow(ax, 2.6, 3.85, 4.0, 3.85)
note(ax, 3.35, 5.15, "可执行 FIFO（默认）：priority DESC,id 扫描\n暂跳冲突者；strict：队首阻塞", ha="center", fontsize=8.4, color=BD, linespacing=1.4)
arrow(ax, 1.6, 4.42, 7.7, 4.42)
note(ax, 3.4, 2.55, "用户主动退出", ha="center", fontsize=8.6, color=BD)
box(ax, 7.0, 1.7, 1.9, 0.8, "SKIPPED\n已跳过", fc="white", fs=9.5, name="4-3SKIP")
arrow(ax, 2.2, 3.4, 7.0, 2.3)
note(ax, 5.0, 3.05, "账号停用 → 跳过", ha="center", fontsize=8.6, color=BD)
note(ax, 8.15, 0.78, "递补落点：默认 CONFIRMED；--hold-window>0 → HELD\n（递补为系统行为，不经审批）", fontsize=8.2, color=G, ha="center", linespacing=1.5)
note(ax, 0.35, 0.25, "占容 = CONFIRMED + HELD；PENDING/WAITING 不占容；候补不计入每周配额（BR13）",
     fontsize=9, color=TX, ha="left")
save(fig, "fig4-3-states.png")

# ---- 图4-4 取消补位时序（重排：生命线左置、说明列右移，避开事务框） ----
fig, ax = canvas(10.5, 6.2, xr=10.5, yr=10)
cols = ["浏览器 A", "HTTP api()", "booking() 事务", "SQLite"]
xs = [1.0, 2.5, 4.0, 5.5]
for x, c in zip(xs, cols):
    note(ax, x, 9.5, c, fontsize=10.5, fontweight="bold", color=G, ha="center")
    ax.plot([x, x], [0.5, 9.2], color="#9db8ad", lw=0.8)
steps = [
    (8.8, "POST /api/reservations/{id}/cancel", 0, 1),
    (8.2, "BEGIN IMMEDIATE", 1, 2),
    (7.6, "UPDATE 预约 → CANCELLED（reason=USER）", 2, 3),
    (7.0, "promote_fill：队首候补 → CONFIRMED（WAITLIST）", 2, 2),
    (6.4, "写入双向通知 + 操作事件", 2, 2),
    (5.8, "COMMIT：取消与补位原子生效", 2, 1),
    (5.2, "200 {reservation_id, promoted_id}", 1, 0),
]
for yy, t, a, b in steps:
    ax.annotate("", xy=(xs[b], yy), xytext=(xs[a], yy),
                arrowprops=dict(arrowstyle="-|>", color=BD, lw=1.1))
    note(ax, 6.3, yy, t, fontsize=9, color=TX, va="center")
ax.add_patch(Rectangle((3.55, 5.55), 0.9, 3.05, fc="none", ec=G, lw=1.4, linestyle="--"))
note(ax, 4.0, 8.85, "事务边界", fontsize=9.5, color=G, ha="center", rotation=0)
note(ax, 4.0, 0.15, "exit 86：COMMIT 前崩溃 → 整体回滚\nexit 87：提交后崩溃 → 结果持久、重放幂等",
        fontsize=8.8, color="#a3562b", ha="center", linespacing=1.6)
save(fig, "fig4-4-cancel-seq.png")

# ---- 图4-5 签到爽约扫描时序（同法重排） ----
fig, ax = canvas(10.5, 6.2, xr=10.5, yr=10)
cols = ["扫描线程", "booking 数据", "候补队列", "通知"]
xs = [1.0, 2.6, 4.2, 5.8]
for x, c in zip(xs, cols):
    note(ax, x, 9.5, c, fontsize=10.5, fontweight="bold", color=G, ha="center")
    ax.plot([x, x], [0.5, 9.2], color="#9db8ad", lw=0.8)
note(ax, 5.25, 8.85, "每 --sweep-interval 秒触发", fontsize=9.5, color=BD, ha="center")
seq = [
    (8.2, "SELECT 到期未签到（窗口 --checkin-window）", 0, 1),
    (7.6, "BEGIN IMMEDIATE", 0, 0),
    (7.0, "UPDATE → CANCELLED（reason=NO_SHOW）", 1, 1),
    (6.4, "写入 NO_SHOW 通知", 1, 3),
    (5.8, "promote_fill：FIFO 补位 + PROMOTED 通知", 2, 2),
    (5.2, "COMMIT（exit 88 注入：整体回滚）", 0, 0),
]
for yy, t, a, b in seq:
    ax.annotate("", xy=(xs[b], yy), xytext=(xs[a], yy),
                arrowprops=dict(arrowstyle="-|>", color=G, lw=1.1))
    note(ax, 6.6, yy, t, fontsize=9, color=TX, va="center")
note(ax, 4.0, 3.9, "补位预约以补位时刻为签到起点\n（防止“刚补位即被判爽约”的循环回收）",
        fontsize=9, color="#a3562b", ha="center", linespacing=1.6)
note(ax, 4.0, 0.15, "与用户请求争用写锁时按 busy_timeout=3000ms 退避；慢语句由 5 秒看门狗打断",
        fontsize=8.8, color=BD, ha="center")
save(fig, "fig4-5-sweep-seq.png")

# ---- 图4-6 请求去重流程图（重排：分支框加宽，标签错位） ----
fig, ax = canvas(9.5, 7.5, xr=10)
box(ax, 3.9, 9.0, 2.4, 0.75, "携带 UUID 的写请求", bold=True, fs=10, name="4-6入口")
box(ax, 3.9, 7.8, 2.4, 0.7, "BEGIN IMMEDIATE", bold=True, fs=10, name="4-6begin")
box(ax, 3.6, 6.3, 3.0, 0.85, "查询 request_receipts\n（用户, 请求编号）", fc="#cfe3d8", fs=9.5, name="4-6查回执")
box(ax, 0.3, 4.5, 2.9, 1.0, "命中且参数一致\n→ 返回已保存原结果", fc="#dbe9e2", fs=9.5, name="4-6重放")
box(ax, 3.55, 4.5, 2.9, 1.0, "命中但参数不同\n→ 409 编号冲突", fc="#f3e3e0", fs=9.5, name="4-6冲突")
box(ax, 6.8, 4.5, 2.9, 1.0, "未命中\n→ 执行业务（容量校验）", fc="white", fs=9.5, name="4-6未命中")
box(ax, 6.8, 2.9, 2.9, 1.0, "回执 + 业务变更 + 事件\n同一事务写入", fc="white", fs=9.5, name="4-6写回执")
box(ax, 3.9, 1.5, 2.4, 0.7, "COMMIT", bold=True, fs=10, name="4-6commit")
box(ax, 0.3, 2.9, 2.7, 1.0, "5xx 不落回执\n→ 原编号安全重试", fc="#f6efdf", fs=9.5, name="4-6重试")
arrow(ax, 5.1, 9.0, 5.1, 8.5); arrow(ax, 5.1, 7.8, 5.1, 7.15)
arrow(ax, 4.6, 6.3, 1.9, 5.5, "参数一致", lx=2.4, ly=6.15)
arrow(ax, 5.1, 6.3, 5.1, 5.5, "参数不同", lx=5.65, ly=5.95)
arrow(ax, 6.6, 6.3, 8.1, 5.5, "未命中", lx=7.9, ly=6.15)
arrow(ax, 8.25, 4.5, 8.25, 3.9); arrow(ax, 7.0, 2.9, 5.4, 2.2)
arrow(ax, 1.7, 4.5, 4.2, 2.2, "重放不执行业务", lx=3.2, ly=2.35, ls="--")
arrow(ax, 1.6, 2.9, 3.9, 1.85, "重试", lx=2.2, ly=2.6)
save(fig, "fig4-6-dedup.png")

# ---- 图6-1 候补策略对比实验（r26 矩阵实测数据）：三策略×四档 ρ 的平均等待时长 + Jain 公平指数 ----
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
rhos = [0.7, 1.0, 1.3, 1.6]
data = {  # docs/evidence/experiment/strategy_matrix.json（2026-09-17 实测）
    "strict":     [0, 0, 0, 0],
    "executable": [7204, 7205, 7207, 7208],
    "weighted":   [3603, 3606, 3606, 3608],
}
import numpy as _np
x = _np.arange(len(rhos)); w = 0.26
colors = {"strict": "#b9c8c1", "executable": G, "weighted": "#d9a441"}
for i, (name, vals) in enumerate(data.items()):
    ax1.bar(x + (i - 1) * w, vals, w, label=name, color=colors[name], edgecolor=BD, linewidth=0.6)
ax1.set_xticks(x); ax1.set_xticklabels([f"ρ={r}" for r in rhos], fontsize=10)
ax1.set_ylabel("候补平均等待（秒）", fontsize=10.5)
ax1.set_title("释放名额的平均等待时长（strict 全部空置 =0）", fontsize=11)
ax1.legend(fontsize=9.5, frameon=False); ax1.spines[["top", "right"]].set_visible(False)
ax1.grid(axis="y", linestyle=":", alpha=0.5)
jain = {"strict": [0.7385, 0.7935, 0.8028, 0.8176], "executable": [0.7319, 0.7801, 0.7893, 0.7940], "weighted": [0.7319, 0.7801, 0.7893, 0.7940]}
for name, vals in jain.items():
    ax2.plot(x, vals, marker="o", markersize=5, linewidth=1.6, label=name, color=colors[name])
ax2.set_xticks(x); ax2.set_xticklabels([f"ρ={r}" for r in rhos], fontsize=10)
ax2.set_ylim(0.70, 0.84); ax2.set_ylabel("Jain 公平指数", fontsize=10.5)
ax2.set_title("Jain 公平指数（按用户获得预约数，1=完全公平）", fontsize=11)
ax2.legend(fontsize=9.5, frameon=False, loc="lower right"); ax2.spines[["top", "right"]].set_visible(False)
ax2.grid(axis="y", linestyle=":", alpha=0.5)
fig.suptitle("三策略 × 四档负载的候补递补对比（确定性矩阵实验，容量 3 × 12 场次）", fontsize=11.5)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(OUT / "fig6-1-strategies.png", dpi=160)
plt.close(fig)
print("saved fig6-1-strategies.png")

print("全部插图生成完毕 →", OUT)
if WARN:
    print("\n".join(WARN))
    raise SystemExit(f"排版校验未通过：{len(WARN)} 处溢出，请调整布局")
print("排版校验：全部文字适配所在框，零溢出")
