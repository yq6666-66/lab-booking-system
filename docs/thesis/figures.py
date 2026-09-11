# -*- coding: utf-8 -*-
"""论文插图生成：6 张示意图（架构/ER/状态转换/时序×2/去重流程），输出 PNG。"""
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

def box(ax, x, y, w, h, text, fc=GB, ec=BD, fs=10, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02", fc=fc, ec=ec, lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=TX, fontweight="bold" if bold else "normal")

def arrow(ax, x1, y1, x2, y2, text="", fs=8.5, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14,
                                 color=BD, lw=1.2, linestyle=ls))
    if text:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.015, text, ha="center", fontsize=fs, color=BD)

def canvas(w=10, h=6.5):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 10); ax.set_ylim(0, 10); ax.axis("off")
    return fig, ax

def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig); print("saved", name)

# ---- 图4-1 系统总体架构 ----
fig, ax = canvas()
box(ax, 0.5, 8.6, 9, 1.0, "浏览器（原生 HTML/JS/CSS：预约 · 候补 · 签到 · 通知 · 管理工作台）", fc="#dbe9e2", bold=True)
ax.text(5, 8.2, "JSON over HTTP（仅 127.0.0.1 回环）", ha="center", fontsize=9, color=BD)
ax.add_patch(FancyArrowPatch((5, 8.5), (5, 7.9), arrowstyle="-|>", mutation_scale=14, color=BD))
box(ax, 0.5, 6.4, 9, 1.4, "CivetWeb 接入层（8 工作线程 · 回环监听 · TLS 就绪）", bold=True)
for i, t in enumerate(["会话认证", "CSRF/Origin/Host 校验", "请求体上限 16KB", "安全响应头"]):
    box(ax, 0.8 + i * 2.25, 6.55, 2.05, 0.55, t, fc="white", fs=8.5)
box(ax, 0.5, 4.2, 9, 1.4, "业务层 src/service.c（BEGIN IMMEDIATE 单写者事务）", bold=True)
for i, t in enumerate(["预约/取消", "容量校验+promote_fill", "请求去重回执", "签到/爽约扫描", "通知/改密/会话"]):
    box(ax, 0.62 + i * 1.82, 4.35, 1.72, 0.55, t, fc="white", fs=8)
box(ax, 6.9, 2.6, 2.6, 0.8, "限流防爆破 ratelimit.c\n（令牌桶+登录锁定）", fc="#f6efdf")
box(ax, 6.9, 1.6, 2.6, 0.8, "运行指标 metrics.c\n（原子计数+延迟直方图）", fc="#f6efdf")
box(ax, 0.5, 2.6, 6.0, 0.8, "结构化日志 log.c（分级 · 轮转 · 访问日志 · 慢请求告警）", fc="#f6efdf")
box(ax, 0.5, 1.2, 9, 1.0, "数据访问 src/db.c（参数绑定 · 语句缓存 · 线程连接复用 · 看门狗 · 幂等迁移）", bold=True)
box(ax, 0.5, 0.1, 9, 0.8, "SQLite 3.53（WAL · synchronous=FULL · 外键 · schema v3 容量制 · 备份 API）", fc="#dbe9e2", bold=True)
for y in (7.9, 6.3, 4.1, 2.5, 1.15):
    ax.add_patch(FancyArrowPatch((5, y), (5, y - 0.25), arrowstyle="-|>", mutation_scale=12, color=BD))
save(fig, "fig4-1-architecture.png")

# ---- 图4-2 ER 图 ----
fig, ax = canvas(11, 7)
def ent(x, y, w, name, fields):
    box(ax, x, y, w, 0.42, name, fc="#cfe3d8", bold=True, fs=9.5)
    ax.add_patch(Rectangle((x, y - 0.09 * len(fields)), w, 0.09 * len(fields), fc="white", ec=BD, lw=1))
    for i, f in enumerate(fields):
        ax.text(x + 0.08, y - 0.07 - i * 0.09, f, fontsize=7.6, va="center", color=TX)
def line(x1, y1, x2, y2, label="", one="1", many=""):
    ax.plot([x1, x2], [y1, y2], color=BD, lw=1)
    if label: ax.text((x1+x2)/2, (y1+y2)/2+0.08, label, fontsize=7.5, color=BD, ha="center")
    if one: ax.text(x1, y1 + (0.12 if y2>y1 else -0.12), one, fontsize=7.5, color=BD)
    if many: ax.text(x2, y2 + (0.12 if y2>y1 else -0.12), many, fontsize=7.5, color=BD)
E = dict(w=1.55)
ent(0.3, 8.6, 1.55, "users", ["id PK","username UQ","password_hash","role/enabled"])
ent(0.3, 6.2, 1.55, "sessions", ["token_hash PK","user_id FK","csrf_token","expires_at"])
ent(2.6, 8.7, 1.7, "labs", ["id PK","name UQ","location","capacity→slots","enabled"])
ent(2.6, 6.2, 1.7, "slots", ["id PK","lab_id FK","start_at/end_at","capacity（v3）","enabled"])
ent(5.1, 8.4, 1.8, "reservations", ["id PK","user_id FK","slot_id FK","status/source","checked_in_at","cancel_reason（v2）"])
ent(5.1, 5.2, 1.8, "waitlist", ["id PK","user_id FK","slot_id FK","status（FIFO）","promoted_id FK"])
ent(8.0, 8.5, 1.7, "request_receipts", ["user_id+req_id PK","action/digest","http_status","result_json"])
ent(8.0, 6.1, 1.7, "operation_events", ["id PK","actor_id FK","action","entity_id/request_id"])
ent(8.0, 3.6, 1.7, "notifications", ["id PK","user_id FK","kind","read_at","slot/reservation FK"])
line(1.85, 8.8, 2.6, 8.9, "1:N 发布")
line(0.9, 8.6, 0.9, 6.62, "1:N 会话")
line(1.85, 8.9, 5.1, 9.0, "1:N 预约", "1", "N")
line(1.85, 8.75, 5.1, 5.9, "1:N 候补")
line(2.75, 8.7, 5.6, 8.4, "1:N 占用")
line(2.75, 6.4, 5.5, 5.9, "1:N 排队")
line(3.45, 6.2, 5.5, 5.5, "slots 1:N")
line(6.9, 8.9, 8.0, 8.9, "去重回执 N:1")
line(6.9, 8.6, 8.0, 6.5, "操作审计 N:1")
line(6.9, 8.3, 8.0, 4.0, "站内通知 N:1")
ax.text(5.5, 2.2, "局部唯一约束：每场次至多 1 条 CONFIRMED×容量（v3 由事务校验）\n每（用户,场次）至多 1 条 WAITING；request_receipts 以（用户,请求编号）为主键实现幂等",
        fontsize=8.5, color=BD, ha="center")
save(fig, "fig4-2-er.png")

# ---- 图4-3 状态转换图 ----
fig, ax = canvas(10, 5.6)
box(ax, 1.0, 4.2, 2.2, 0.9, "CONFIRMED\n有效预约", fc="#cfe3d8", bold=True)
box(ax, 6.5, 4.2, 2.4, 0.9, "CANCELLED\n已取消", fc="#f3e3e0", bold=True)
box(ax, 1.0, 1.0, 2.2, 0.9, "WAITING\n候补排队", fc="#fdf3d8", bold=True)
box(ax, 6.5, 1.9, 2.0, 0.8, "PROMOTED\n已补位", fc="#dbe9e2")
box(ax, 6.5, 0.6, 2.0, 0.8, "WITHDRAWN\n已退出", fc="white")
box(ax, 3.9, 0.2, 1.8, 0.8, "SKIPPED\n已跳过", fc="white")
arrow(ax, 3.2, 4.65, 6.5, 4.65, "用户取消（reason=USER）")
arrow(ax, 3.2, 4.35, 6.5, 4.9, "爽约扫描超时（reason=NO_SHOW，exit 注入点）")
arrow(ax, 3.2, 4.2, 7.5, 2.7, "取消/爽约 → promote_fill 按 FIFO 连续补位至满员")
arrow(ax, 3.2, 1.45, 6.5, 2.3, "入队顺序 + 席位释放")
arrow(ax, 3.2, 1.2, 6.5, 1.0, "用户主动退出")
arrow(ax, 3.2, 1.0, 3.9, 0.6, "账号禁用 → 跳过")
ax.text(5.0, 3.6, "promote_fill：while 已约<容量 且 队列非空", fontsize=8.5, color=BD)
ax.text(0.4, 2.8, "同一事务内：\n取消 / 补位\n（无中间态）", fontsize=9, color=G, fontweight="bold")
save(fig, "fig4-3-states.png")

# ---- 图4-4 取消补位时序 ----
fig, ax = canvas(10, 6)
cols = ["浏览器 A", "HTTP api()", "booking() 事务", "SQLite"]
for i, c in enumerate(cols): ax.text(1.2 + i * 2.5, 9.5, c, fontsize=10, fontweight="bold", color=G, ha="center")
for i in range(4): ax.plot([1.2 + i * 2.5, 1.2 + i * 2.5], [0.3, 9.2], color="#9db8ad", lw=0.8)
steps = [
    (8.8, "POST /api/reservations/{id}/cancel"), (8.3, "BEGIN IMMEDIATE"),
    (7.8, "UPDATE 预约 → CANCELLED(reason=USER)"), (7.3, "promote_fill：队首候补 → CONFIRMED(WAITLIST)"),
    (6.8, "写入双向通知 + 操作事件"), (6.3, "COMMIT（取消与补位原子生效）"),
    (5.8, "200 {reservation_id, promoted_id}"),
]
for y, t in enumerate(steps, start=0):
    yy = steps[y][0]
    ax.annotate("", xy=(1.2 + (y % 2) * 2.5, yy), xytext=(1.2 + ((y + 1) % 2) * 2.5, yy),
                arrowprops=dict(arrowstyle="-|>", color=BD, lw=1.1))
    ax.text(5.6, yy + 0.06, t, fontsize=8.8, color=TX)
ax.add_patch(Rectangle((3.35, 5.95), 2.55, 3.3, fc="none", ec=G, lw=1.4, linestyle="--"))
ax.text(4.6, 9.35, "事务边界", fontsize=9, color=G, ha="center")
ax.text(1.2, 0.05, "exit 86 注入：COMMIT 前崩溃 → 整体回滚；exit 87：提交后响应前崩溃 → 结果持久、重放幂等",
        fontsize=8.5, color="#a3562b")
save(fig, "fig4-4-cancel-seq.png")

# ---- 图4-5 签到爽约扫描时序 ----
fig, ax = canvas(10, 6)
cols = ["扫描线程 sweeper", "booking 数据", "候补队列", "通知"]
for i, c in enumerate(cols): ax.text(1.4 + i * 2.4, 9.5, c, fontsize=10, fontweight="bold", color=G, ha="center")
for i in range(4): ax.plot([1.4 + i * 2.4, 1.4 + i * 2.4], [0.3, 9.2], color="#9db8ad", lw=0.8)
seq = [
    (8.7, "每 --sweep-interval 秒触发", "note"),
    (8.1, "SELECT 到期未签到（窗口 --checkin-window）"),
    (7.5, "BEGIN IMMEDIATE"),
    (7.0, "UPDATE → CANCELLED(reason=NO_SHOW)"),
    (6.5, "写入 NO_SHOW 通知"),
    (6.0, "promote_fill：FIFO 补位 + PROMOTED 通知"),
    (5.5, "COMMIT（exit 88 注入：整体回滚）"),
]
for i, (yy, t, *rest) in enumerate(seq):
    if rest and rest[0] == "note":
        ax.text(5.5, yy, t, fontsize=9, color=BD, ha="center"); continue
    ax.annotate("", xy=(1.4, yy), xytext=(1.4, yy + 0.5), arrowprops=dict(arrowstyle="-|>", color=G, lw=1.1))
    ax.text(4.4, yy + 0.06, t, fontsize=8.8, color=TX)
ax.text(5.5, 4.6, "补位预约以补位时刻为签到起点（防止刚补位即被判爽约的死循环）", fontsize=8.8, color="#a3562b", ha="center")
ax.text(1.4, 0.05, "与用户请求争用写锁时按 busy_timeout=3000ms 退避；慢语句由 5 秒看门狗打断", fontsize=8.5, color=BD)
save(fig, "fig4-5-sweep-seq.png")

# ---- 图4-6 请求去重流程图 ----
fig, ax = canvas(9, 7)
box(ax, 3.5, 9.0, 2.2, 0.7, "携带 UUID 的写请求", bold=True)
box(ax, 3.5, 7.7, 2.2, 0.7, "BEGIN IMMEDIATE")
box(ax, 3.2, 6.3, 2.8, 0.8, "查询 request_receipts\n(用户,请求编号)", fc="#cfe3d8")
box(ax, 0.4, 4.6, 2.4, 0.9, "命中且参数一致\n→ 返回已保存原结果", fc="#dbe9e2")
box(ax, 3.4, 4.6, 2.4, 0.9, "命中但参数不同\n→ 409 REQUEST_ID_CONFLICT", fc="#f3e3e0")
box(ax, 6.4, 4.6, 2.4, 0.9, "未命中\n→ 执行业务（容量/状态校验）", fc="white")
box(ax, 6.4, 3.0, 2.4, 0.9, "写入回执 + 业务变更 + 事件\n（同事务）", fc="white")
box(ax, 3.5, 1.6, 2.2, 0.7, "COMMIT", bold=True)
box(ax, 0.6, 2.9, 2.0, 0.7, "500/503 不落回执\n→ 原编号安全重试", fc="#f6efdf")
arrow(ax, 4.6, 9.0, 4.6, 8.4); arrow(ax, 4.6, 7.7, 4.6, 7.1)
arrow(ax, 4.4, 6.5, 1.6, 5.5, "是"); arrow(ax, 4.8, 6.5, 4.6, 5.5, "参数不同")
arrow(ax, 6.0, 6.5, 7.6, 5.5, "否")
arrow(ax, 7.6, 4.6, 7.6, 3.9); arrow(ax, 7.2, 3.0, 4.9, 2.0)
arrow(ax, 1.6, 4.6, 3.3, 2.0, "重放不执行业务", ls="--")
arrow(ax, 2.0, 2.9, 3.4, 2.0)
save(fig, "fig4-6-dedup.png")
print("全部插图生成完毕 →", OUT)
