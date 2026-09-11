# -*- coding: utf-8 -*-
"""毕业论文初稿生成：docs/thesis/毕业论文初稿.docx（通用学术格式，学校模板后补可套排）。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
import pathlib

FIG = pathlib.Path(__file__).resolve().parent / "figures"
doc = Document()

# ---------- 样式 ----------
def set_font(style, name, size, bold=False):
    style.font.name = name; style.font.size = Pt(size); style.font.bold = bold
    style.element.get_or_add_rPr()
    rf = style.element.rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), name)
for sec in doc.sections:
    sec.top_margin = sec.bottom_margin = Cm(2.54); sec.left_margin = sec.right_margin = Cm(3.0)
set_font(doc.styles["Normal"], "宋体", 12)
set_font(doc.styles["Heading 1"], "黑体", 16, True)
set_font(doc.styles["Heading 2"], "黑体", 14, True)
set_font(doc.styles["Heading 3"], "黑体", 12, True)

def h1(t):
    p = doc.add_heading(t, level=1); p.paragraph_format.space_before = Pt(18); p.paragraph_format.space_after = Pt(10)
    return p
def h2(t): return doc.add_heading(t, level=2)
def h3(t): return doc.add_heading(t, level=3)
def para(t, indent=True, align=None, size=12, bold=False):
    p = doc.add_paragraph(); run = p.add_run(t); run.font.size = Pt(size); run.font.bold = bold
    run.font.name = "宋体"; run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    if indent: p.paragraph_format.first_line_indent = Cm(0.74)
    if align: p.alignment = align
    return p
def li(t):
    p = para(t, indent=False); p.paragraph_format.left_indent = Cm(0.74); return p
def cap(t):
    p = doc.add_paragraph(); r = p.add_run(t); r.font.size = Pt(10.5); r.font.bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
def img(name, width=14.5, cap_text=None):
    doc.add_picture(str(FIG / name), width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if cap_text: cap(cap_text)
def tbl(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers)); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, htxt in enumerate(headers):
        c = t.rows[0].cells[i]; c.text = ""
        r = c.paragraphs[0].add_run(htxt); r.font.bold = True; r.font.size = Pt(10.5)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v)); r.font.size = Pt(10.5)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows: row.cells[i].width = Cm(w)
    return t

# ---------- 封面 ----------
for _ in range(4): doc.add_paragraph()
p = para("本科毕业设计（论文）", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=22, bold=True)
doc.add_paragraph()
p = para("基于 C 语言的实验室预约与候补系统的设计与实现", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=18, bold=True)
doc.add_paragraph(); doc.add_paragraph()
for line in ["学    院：＿＿＿＿＿＿＿＿＿＿", "专    业：软件工程", "姓    名：＿＿＿＿＿＿＿＿＿＿",
             "学    号：＿＿＿＿＿＿＿＿＿＿", "指导教师：＿＿＿＿＿＿＿＿＿＿", "完成日期：2026 年 6 月"]:
    para(line, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
doc.add_page_break()

# ---------- 摘要 ----------
h1("摘  要")
para("高校开放实验室普遍存在预约冲突、占而不用、候补无序等问题。本课题设计并实现了一个基于 C 语言的实验室预约与候补管理系统，"
     "采用 C11 与 CivetWeb、SQLite、cJSON、libsodium 等嵌入式组件构建浏览器/服务器架构，重点解决三个工程问题：并发争抢下的容量一致性、"
     "重复请求与中断恢复的幂等性、签到爽约与候补补位的业务闭环。")
para("系统以单写者事务（BEGIN IMMEDIATE）配合容量不变量保证并发正确性；以持久化请求回执实现同编号同参数重放幂等、同编号异参数冲突拒绝；"
     "以服务端扫描线程在单事务内完成爽约回收与先进先出补位；并实现了登录防爆破、令牌桶限流、站内通知、在线会话管理与统计导出。"
     "系统通过五层自动化验证：20 个单元测试、31 项集成断言组、720 次并发争抢请求（零超卖）、20 次进程中断恢复实验（三类注入点，状态与重放全部正确）、"
     "以及模糊稳健性与浸泡稳定性实验。连接复用与语句缓存优化使读路径吞吐提升最高 600%，p50 延迟下降 86%。")
para("结果表明，在不引入重型运行时的前提下，以规范的事务设计、持久化去重与自动化实验体系，C 语言同样能够构建出具备工业级可靠性论证的"
     "中小型 Web 业务系统。")
para("关键词：实验室预约；候补队列；事务一致性；幂等请求；故障注入；C 语言", bold=True)
doc.add_paragraph()
h1("Abstract")
para("University laboratories commonly suffer from booking conflicts, no-shows and disordered waiting lists. This project designs and "
     "implements a laboratory reservation and waitlist management system in C (C11), built on CivetWeb, SQLite, cJSON and libsodium with a "
     "browser/server architecture. It focuses on three engineering problems: capacity consistency under concurrent contention, idempotency "
     "against duplicated requests and process crashes, and the business loop of check-in, no-show recovery and FIFO promotion.")
para("The system guarantees concurrent correctness with single-writer transactions (BEGIN IMMEDIATE) plus a per-slot capacity invariant; "
     "achieves idempotency through persisted request receipts; and completes no-show reclamation and promotion inside one transaction in a "
     "server-side sweeper. It also provides login brute-force protection, token-bucket rate limiting, in-app notifications, online session "
     "management and statistics export. The system passes five layers of automated verification: 20 unit tests, 31 integration assertion "
     "groups, 720 concurrent contention requests (zero overbooking), 20 crash-recovery trials across three fault injection points, and fuzz "
     "plus soak stability experiments. Connection reuse and statement caching raise read throughput by up to 600% and cut p50 latency by 86%.")
para("The results show that, with disciplined transaction design, persisted deduplication and an automated experiment system, C can deliver "
     "a small-to-medium web application whose reliability is demonstrably verified without heavy runtimes.")
para("Key words: laboratory reservation; waitlist queue; transactional consistency; idempotent request; fault injection; C language", bold=True)
doc.add_page_break()

# ---------- 第1章 ----------
h1("第 1 章 绪论")
h2("1.1 选题背景与意义")
para("随着高校实践教学规模扩大，开放实验室的机时资源日益紧张。人工或简易电子表格管理方式存在三类典型问题：其一，多个用户同时申请同一"
     "时段时缺乏冲突防护，容易出现重复占用；其二，预约后不按时到场（爽约）导致资源闲置，而真正有需求的人却在排队；其三，候补缺乏公平的"
     "排队与自动补位机制，释放的名额不能及时流转。")
para("将预约流程线上化并辅以自动化的候补、签到与爽约管理，可以显著提高机时利用率与管理效率。同时，预约系统天然包含并发争抢、重复提交、"
     "进程异常退出等工程难题，是软件工程专业综合运用数据库事务、网络编程、并发控制与软件测试知识的理想载体。")
para("选择 C 语言作为实现语言具有双重意义：一方面，C 贴近系统底层，资源与错误的处理全部显式，能够完整展现从 HTTP 报文到磁盘字节的全链路；"
     "另一方面，在缺少垃圾回收与重型框架的情况下保证正确性，对工程纪律与测试体系提出了更高要求，其方法与结论更具普适价值。")
h2("1.2 国内外研究与应用现状")
para("开源社区已有 LibreBooking、LabArchives 等实验室预约系统。这类系统功能完备，但多为 PHP/Java 大型框架产物，部署依赖较重，二次开发"
     "门槛高；其核心的冲突防护通常依赖应用层检查与数据库约束的组合，对并发正确性的论证着墨不多。商用平台（如各类机房管理系统）则多为闭源"
     "黑盒，难以审计与定制。")
para("在工程实践层面，以 SQLite 为代表的嵌入式数据库证明了单文件、零配置存储在中小业务中的可靠性；CivetWeb 等嵌入式 HTTP 服务器则表明"
     "C 语言可以以极低的依赖成本提供完整的 Web 服务能力。将两者结合构建业务系统，并以自动化实验体系验证其正确性，是一个有价值且可行的"
     "工程方向。")
h2("1.3 主要工作")
para("本课题完成的主要工作如下：")
li("（1）设计并实现了完整的实验室预约业务闭环：容量制场次查询与预约、FIFO 候补、取消自动补位、限时签到与爽约回收、站内通知、统计导出；")
li("（2）设计并实现了两项关键机制：基于持久化请求回执的幂等去重（同编号同参数重放、同编号异参数冲突）、以及单写者事务内的取消—补位原子操作；")
li("（3）构建了五层自动化验证体系：单元测试、31 项集成断言组（含 720 次并发争抢与 20 次故障注入恢复）、模糊稳健性实验、浸泡稳定性实验与"
     "静态分析/加固构建；")
li("（4）完成性能优化实验：每线程连接复用与语句缓存使读路径吞吐最高提升 600%，并以优化前后基准对比量化收益。")
h2("1.4 论文组织结构")
para("第 1 章绪论；第 2 章介绍相关技术；第 3 章进行需求分析；第 4 章阐述系统设计；第 5 章说明系统实现；第 6 章给出测试与实验结果；"
     "第 7 章总结全文并展望后续工作。")

# ---------- 第2章 ----------
h1("第 2 章 相关技术")
h2("2.1 C11 与 MinGW-w64")
para("系统采用 C11 标准与 MinGW-w64 GCC 15.2 工具链构建，使用 _Thread_local 线程局部存储实现每工作线程的数据库连接复用，"
     "并以 SRWLOCK（读写锁）保护进程内共享状态。构建脚本提供正式版、故障注入测试版（TEST_FAULTS 宏）、加固版（_FORTIFY_SOURCE=3、"
     "栈保护、自动变量零初始化）与覆盖率版四种目标。")
h2("2.2 CivetWeb 嵌入式 HTTP 服务器")
para("CivetWeb 是衍生自 Mongoose 的开源嵌入式 Web 服务器，以单线程库形态嵌入宿主程序，提供多工作线程、静态文件服务、Cookie 解析与"
     "SSL 能力。系统以 NO_SSL 宏裁剪 TLS（传输安全留作后续扩展），固定 8 个工作线程并仅监听回环地址，符合校园单机部署场景。")
h2("2.3 SQLite 与事务模型")
para("SQLite 是无服务器的嵌入式关系数据库。系统启用 WAL（写前日志）模式实现读写并行，synchronous=FULL 保证掉电持久性；"
     "所有业务写操作置于 BEGIN IMMEDIATE 短事务中——该模式在事务开始时即获取写锁，将并发冲突转化为串行化排队，是本系统并发正确性的基石。"
     "局部唯一索引（部分索引）用于声明数据不变量；Online Backup API 用于在线备份。")
h2("2.4 cJSON 与 libsodium")
para("cJSON 提供标准 C 的 JSON 解析与序列化，其解析深度限制可抵御嵌套嵌套攻击；libsodium 提供密码学安全的随机数生成与 Argon2id"
     "口令哈希（crypto_pwhash），避免自研密码学带来的风险。")
h2("2.5 前端技术")
para("前端采用无框架的原生 HTML/CSS/JavaScript 单页实现，与嵌入式后端在依赖哲学上保持一致：全部资源本地静态服务，无构建步骤、"
     "无第三方运行时，页面总量不足 60 KB。")

# ---------- 第3章 ----------
h1("第 3 章 需求分析")
h2("3.1 角色与用例")
para("系统包含两类角色。普通用户：查询开放场次、预约与取消、加入/退出候补、签到、接收站内通知、自助修改密码、管理本人在线会话；"
     "管理员：维护实验室、按日期区间与容量发布场次、查看全体记录与操作日志、查看逐日统计并导出 CSV、查看运行指标。账号由管理员初始化"
     "预置，本系统不开放注册。")
h2("3.2 功能需求")
para("核心功能需求归纳为：场次查询（按实验室与日期，返回容量与实时占用）；预约（容量内即时生效）；候补（满员排队，按入队顺序排队）；"
     "取消与自动补位（释放与补位原子完成）；替代时段提示（满员时返回同实验室 7 天内空闲场次）；限时签到与爽约回收（超时自动释放并补位）；"
     "站内通知（补位与爽约双向提醒）；账号自助管理（改密、会话下线）；记录分页与统计导出。")
h2("3.3 业务规则")
para("系统的业务不变量归纳为以下规则（BR）：")
li("BR1 容量约束：任一场次同一时刻的有效预约数不超过其容量 capacity（1..200）；")
li("BR2 唯一参与：同一用户对同一场次至多持有一条有效预约或一条有效候补；")
li("BR3 FIFO 补位：名额释放与候补补位在同一事务内按候补编号递增顺序连续进行，直至满员或队列空；")
li("BR4 幂等去重：每次写操作携带全局唯一的请求编号；同编号同参数重放返回已保存原结果，同编号异参数返回冲突；失败（5xx）不落回执，可安全重试；")
li("BR5 签到窗口：场次开始后进入签到窗口（可配置），仅本人可签到；窗口过后不可补签，由扫描线程自动爽约回收；")
li("BR6 爽约语义：爽约回收与候补补位在同一事务内完成，并向预约人与候补人各写入一条通知；补位预约以其补位时刻为签到起点；")
li("BR7 权限边界：预约、候补、签到、改密仅限本人；管理接口仅限管理员；")
li("BR8 防爆破与限流：登录连续失败达到阈值后按用户名锁定；已登录用户的全部写操作受令牌桶限速；")
li("BR9 历史冻结：场次开始后禁止除签到外的一切写操作，历史记录与历史重放不受影响。")
h2("3.4 非功能需求")
para("并发正确性（争抢下不超卖）、可用性（进程异常退出后数据一致、可恢复）、安全性（口令哈希、会话与请求伪造防护、防爆破）、"
     "可观测性（分级日志、访问日志、运行指标）、可维护性（接口契约文档、schema 幂等迁移、五层自动化测试）。")

# ---------- 第4章 ----------
h1("第 4 章 系统设计")
h2("4.1 总体架构")
para("系统采用五层架构，如图 4-1 所示：浏览器层负责交互；接入层由 CivetWeb 提供 HTTP 解析与多线程调度，并完成会话认证与请求伪造防护；"
     "业务层以单写者事务组织全部写路径；数据访问层封装参数绑定、语句缓存、连接复用与 schema 迁移；存储层为启用 WAL 的 SQLite。"
     "横切模块包括限流防爆破、运行指标与结构化日志。")
img("fig4-1-architecture.png", cap_text="图 4-1 系统总体架构")
para("安全边界方面，服务仅监听 127.0.0.1 回环地址，供本机浏览器访问；全部响应携带统一安全响应头（X-Frame-Options: DENY、"
     "Content-Security-Policy: default-src 'self'、Referrer-Policy: no-referrer）；请求体上限 16 KB 且拒绝重复键。")
h2("4.2 数据库设计")
para("数据模型共 9 张表，如图 4-2 所示。reservations（预约）与 waitlist（候补）是核心业务表；request_receipts（请求回执）支撑幂等去重；"
     "operation_events（操作事件）提供审计；notifications（通知，v2 引入）承载站内提醒。schema 采用版本化幂等迁移：v2 追加签到与取消原因"
     "列及通知表，v3 为场次表追加 capacity 列并退役单占用唯一索引，全部迁移只追加、不重建表，并以构造旧版本数据库的方式实测升级路径。")
img("fig4-2-er.png", cap_text="图 4-2 数据库 ER 图（schema v3）")
h2("4.3 状态转换设计")
para("预约状态机包含 CONFIRMED 与 CANCELLED 两个状态，取消原因区分为 USER（主动取消）与 NO_SHOW（爽约）；候补状态机包含 WAITING、"
     "WITHDRAWN、PROMOTED、SKIPPED 四个状态。转换全部由显式业务动作或扫描线程触发，如图 4-3 所示。")
img("fig4-3-states.png", cap_text="图 4-3 预约与候补状态转换图")
h2("4.4 关键机制设计")
h3("4.4.1 并发一致性：单写者事务与容量不变量")
para("v1 版本曾以部分唯一索引（每场次至多一条 CONFIRMED）作为并发防线的最后兜底。引入容量制后，唯一索引无法表达\u201c至多 capacity 条\u201d"
     "的约束，系统改为完全依赖 BEGIN IMMEDIATE 单写者串行化：同一时刻至多一个写事务，事务内先读取已约数再校验容量，因此容量不变量"
     "\u201c已约数 ≤ capacity\u201d在任意提交序列下均成立。该设计将正确性从\u201c数据库约束兜底\u201d演进为\u201c事务协议保证\u201d，"
     "并在第六章以 720 次并发争抢实验与超容量落库检查加以验证。")
h3("4.4.2 请求去重：持久化回执")
para("客户端为每个写操作生成一次 UUID 请求编号。服务端将（用户，编号）→（动作，参数摘要，HTTP 状态，结果 JSON）作为回执与业务变更在同一"
     "事务内持久化；再次收到同编号请求时：同参数直接重放原结果（含 409 类失败结果），异参数返回 REQUEST_ID_CONFLICT。网络超时、503 等临时"
     "失败不落回执，客户端可携带原编号安全重试，从而以\u201c至少一次提交 + 服务端幂等\u201d替代脆弱的\u201c恰好一次\u201d假设。判定流程如图 4-6。")
img("fig4-6-dedup.png", cap_text="图 4-4 请求去重与回执重放流程", width=12.5)
h3("4.4.3 取消补位与爽约回收")
para("取消预约与候补补位在同一事务内完成：先将目标预约置为 CANCELLED，再由 promote_fill 循环按 FIFO 依次将队首有效候补转为 CONFIRMED，"
     "直至满员或队列空；禁用账号自动跳过。爽约回收由扫描线程执行：场次开始后超过签到窗口仍未签到的预约被批量标记为 NO_SHOW 并释放，"
     "同一事务内完成补位与双向通知。补位预约以补位时刻作为签到起点，避免了\u201c刚补位即超时\u201d的死循环。取消时序与扫描时序分别如图 4-4、"
     "图 4-5 所示。")
img("fig4-4-cancel-seq.png", cap_text="图 4-5 取消—补位时序图（标注事务边界）")
img("fig4-5-sweep-seq.png", cap_text="图 4-6 签到—爽约扫描时序图")
h3("4.4.4 会话与传输安全")
para("登录成功后签发 64 位十六进制随机令牌作为 Cookie（HttpOnly、SameSite=Strict），服务端仅保存令牌摘要；全部 POST 请求须携带与令牌"
     "绑定的 CSRF 令牌，浏览器 Origin 必须等于回环服务地址。口令以 libsodium crypto_pwhash（Argon2id）加盐哈希存储。改密成功后除当前会话外"
     "的全部会话立即失效。")

# ---------- 第5章 ----------
h1("第 5 章 系统实现")
h2("5.1 开发环境与构建")
para("开发环境为 Windows x64 + MinGW-w64 GCC 15.2，代码约 1,700 行 C 与 50 行前端脚本。构建脚本支持四个目标：正式版（自动运行 20 个"
     "单元测试）、TEST_FAULTS 测试版（内置三类故障注入点：提交前崩溃 86、提交后响应前崩溃 87、扫描事务中途崩溃 88）、加固版（FORTIFY、"
     "栈保护与自动变量零初始化）与覆盖率版（--coverage，配合优雅停机信号 SIGBREAK 刷写 gcov 数据）。")
h2("5.2 HTTP 接入与会话安全")
para("api() 处理器依次执行 Host 白名单、方法检查、健康探测、Origin 校验、请求体上限与 JSON 合法性检查（含重复键拒绝）、数据库连接获取、"
     "会话认证与 CSRF 校验后才进入业务分发。登录接口前置防爆破闸门：同一用户名连续失败达到阈值（默认 5 次）后锁定 900 秒，期间正确口令"
     "同样返回 429；已登录用户的全部写操作经过每用户令牌桶（默认突发 30、每秒补充 1）限速。")
h2("5.3 事务与去重核心")
para("booking() 是全部写操作的汇聚点，其主干为：BEGIN IMMEDIATE → 用户可用性检查 → 回执查询（命中即重放/冲突）→ 目标与状态校验 → "
     "容量与唯一性校验 → 业务写入与补位 → 回执持久化 → COMMIT；任何一步出错则整体 ROLLBACK 并将临时错误映射为 503。"
     "候补场景下，promote_fill 循环先按 FIFO 将队列补至满员，再处理请求者本人：若请求者恰为队首，其候补先行生效，本次直接预约返回"
     "409 并附替代时段，保证 FIFO 公平性不被绕过。")
h2("5.4 性能优化：连接复用与语句缓存")
para("初版实现每个请求新建 SQLite 连接（open + 两条 PRAGMA + close），并对每条 SQL 重复 prepare。第六轮将其优化为每工作线程复用连接"
     "（_Thread_local，致命错误自动重建）并引入按 SQL 文本的预处理语句 LRU 缓存（每连接上限 32 条，使用后 reset 复用、出错即淘汰）。"
     "优化前后的基准对比见 6.6 节：读路径吞吐最高提升 600%。")
h2("5.5 签到、爽约与通知实现")
para("签到接口校验本人、CONFIRMED 状态与窗口期，以服务端时间落库，同编号重放与重复签到均返回首次签到时间。扫描线程以可配置间隔触发，"
     "单事务内完成\u201c标记 NO_SHOW → 写通知 → 释放席位 → FIFO 补位 → 写 PROMOTED 通知\u201d全流程，并在提交前留有故障注入点。"
     "补位预约的签到起点取其补位创建时刻，与场次的自然开始时间解耦。")
h2("5.6 分页、统计导出与其他")
para("个人与全体记录支持 page/page_size/has_more 分页；统计按北京日聚合七项计数，CSV 导出带 BOM 与合计行以兼容 Excel 直接打开；"
     "前端以上一轮请求编号持久化（sessionStorage）实现跨刷新的重试提示，全部状态变更按钮在确认结果前保持锁定，避免重复提交。")

# ---------- 第6章 ----------
h1("第 6 章 系统测试")
h2("6.1 测试策略与环境")
para("测试体系分五层：（1）Unity 单元测试 20 个，不经 HTTP 直接链接业务层与数据层；（2）集成实验 31 项断言组，以独立 Python 客户端"
     "驱动真实服务进程，覆盖功能、安全、并发与故障恢复；（3）模糊稳健性实验与浸泡稳定性实验；（4）静态保障：gcc -fanalyzer、绑定参数"
     "类型静态核查与加固构建；（5）真实浏览器端到端验收。所有实验使用独立临时端口与全新数据库副本，原始证据（数据库、日志、结果 JSON）"
     "完整归档，失败样本不销毁。")
para("需要如实说明的工具链限制：本机 MinGW-w64 发行版不含 libasan/libubsan 运行库，动态内存检查不可用；作为替代，本地采用加固构建"
     "（FORTIFY + 栈保护 + 自动变量零初始化）并在 CI 中提供 MSYS2 ASan 通道（观察期）。")
h2("6.2 单元测试与覆盖率")
para("单元测试覆盖纯函数（编号/UUID/日期/令牌/分页参数）、数据库不变量（种子形状、超容量检测、脏数据发现、v2→v3 迁移实测）、"
     "业务层直调（预约冲突、替代时段对账、候补 FIFO、签到、通知、会话、改密）、限流纯函数（令牌桶边界与补充数学、登录锁定到期）以及"
     "语句缓存（命中、LRU 淘汰、参数重绑、错误不污染）与线程连接复用（致命错误重建），共 20 个用例。")
para("以 --coverage 构建运行全部单元测试与接口走查后，各源文件行覆盖率见表 6-1（gcov 统计）。")
cap("表 6-1 源文件行覆盖率（单元 + 接口走查）")
tbl(["源文件", "db.c", "http.c", "log.c", "main.c", "metrics.c", "ratelimit.c", "service.c", "util.c"],
    [["行覆盖率 %", 92.95, 91.98, 84.62, 84.62, 93.10, 85.25, 91.46, 98.00]])
h2("6.3 功能、安全与容量回归")
para("集成实验 T01–T22、T25–T27 覆盖登录授权、占用唯一、候补 FIFO 与重入队尾、重放幂等、越权与 CSRF/Origin、畸形与超长输入、过期场次、"
     "外部写锁退避、禁用候补跳过、替代时段对账、统计对账、会话清理、签到窗口、分页参数、CSV 导出、改密会话失效、混合负载连接复用等场景，"
     "最近一轮全部通过。典型新增用例如下：")
li("T13 替代时段：预约已满返回的替代列表与独立 SQL 对账一致（同实验室、7 天内、升序、至多 3 个、全部空闲），且首个替代可直接预约；")
li("T14 运行指标：计数随请求递增，越权访问被拒；")
li("T26 容量制：capacity=3 场次第 4 人返回 409，取消触发 FIFO 补位，已约者再约返回 ALREADY_RESERVED；")
li("T15 过期会话：过期令牌 401，重新登录触发清理，无残留行。")
h2("6.4 并发争抢实验")
para("并发实验以 1、5、10、20 个独立客户端在线程屏障对齐后同时申请同一空闲场次，每档 20 轮共 720 次请求，结果见表 6-2：每轮成功预约数"
     "与数据库占用数恒为 1，其余请求全部收到明确的 409，未出现重复占用或忙碌失败。")
cap("表 6-2 并发争抢实验结果（每档 20 轮）")
tbl(["并发数", "请求数", "成功预约", "409 冲突", "503 忙碌", "p50 中位 (ms)", "p95 (ms)"],
    [[1, 20, 20, 0, 0, 5.7, 9.9], [5, 100, 100, 0, 0, 6.9, 32.4], [10, 200, 200, 0, 0, 9.4, 99.4], [20, 400, 400, 0, 0, 13.6, 210.8]])
para("该实验同时是容量制正确性的直接证据：容量为 1 的场次在多线程争抢下仍未发生超卖，证明单写者事务协议可以完全替代唯一索引兜底。")
h2("6.5 故障注入恢复实验")
para("故障注入覆盖三类中断点（各 10 轮）：事务提交前崩溃（exit 86，取消已执行、补位未完成）、事务提交后响应前崩溃（exit 87）、"
     "以及扫描事务中途崩溃（exit 88）。三轮实验的结论一致：未提交事务整体回滚（预约保持 CONFIRMED、候补保持 WAITING）；已提交事务完整"
     "保留（补位与通知不丢失）；重启后以原请求编号重试，首次重试完成剩余业务，重复重放返回同一结果；每轮重启后数据库完整性检查通过。")
h2("6.6 性能优化对比实验")
para("以连接复用与语句缓存为唯一变量的前后对比实验（同机同参）结果见表 6-3。读路径收益最大：20 并发吞吐由 451 rps 提升至 3155 rps"
     "（+599.5%），p50 由 41.71ms 降至 5.73ms；写路径受限于写锁串行化，收益收窄但依然正向。")
cap("表 6-3 优化前后基准对比（节选，完整数据见 docs/evidence/benchmark/before-after/）")
tbl(["实验", "场景", "并发", "基线 rps", "优化 rps", "吞吐提升", "p50 变化"],
    [["E1", "read", 10, 604.7, 2576.6, "+326%", "-74.4%"],
     ["E1", "read", 20, 451.0, 3154.6, "+599%", "-86.3%"],
     ["E2", "diff-slot", 20, 185.3, 204.4, "+10%", "-35.8%"],
     ["E2", "same-slot", 20, 176.7, 231.6, "+31%", "-32.6%"]])
h2("6.7 稳健性与稳定性实验")
para("模糊实验以 4 线程对登录、预约、候补、改密接口持续发送畸形 JSON、超长字段、错类型、二进制垃圾与并发错乱序列共 240 次：全部请求"
     "得到合法封套响应（4xx 为主），进程存活，SQLite 完整性检查通过。浸泡实验以混合负载运行 60 秒冒烟（34,970 请求、0 错误、0 5xx），"
     "进程工作集增长仅 3 MB、句柄增长 44，未观察到泄漏迹象。")
h2("6.8 调试过程记录")
para("开发过程中依据测试暴露并修复了多个真实缺陷，其现象与修复过程如下：")
li("（1）SQL 绑定格式串错位（两例）：sessions_list 将字符串参数按 'is' 中的 i（8 字节）绑定，/api/slots 查询漏写占位符，前者导致进程"
     "段错误，后者导致查不到任何场次——以最小化二分与调试构建定位；")
li("（2）变参宽度错位：publish_slots 以 int 形参经变参传递给按 8 字节读取的绑定宏，种子初始化报 CHECK 约束失败——统一改用 Id 传递；")
li("（3）爽约—补位死循环：补位产生的新预约以场次开始时间为签到起点，刚补位即再次被判超时回收——改为以补位时刻为起点；")
li("（4）限流桶初始化哨兵碰撞：以 last_ms==0 兼作\u201c未初始化\u201d标记与合法时间戳 0，t=0 边界单测暴露后改用显式 started 标志；")
li("（5）迁移回填遗漏：历史取消记录缺 cancel_reason 会使完整性检查失败——迁移中统一回填为 USER，并以构造 v1/v2 旧库实测升级。")
para("上述缺陷均由自动化测试先行暴露，修复后以新增回归用例固化，验证了\u201c测试驱动发现—最小复现—修复—回归固化\u201d流程的有效性。")
h2("6.9 浏览器端到端验收")
para("以真实 Chromium 浏览器完成三轮端到端验收：走通登录、查询、预约、候补、取消补位、签到、通知已读、改密与会话下线、分页、统计与 "
     "CSV 下载、容量显示与替代时段改约、指标面板等全部交互路径，截图存证于 docs/evidence/ui-r3/ 与 ui-r5/。")

# ---------- 第7章 ----------
h1("第 7 章 总结与展望")
h2("7.1 工作总结")
para("本课题面向高校开放实验室管理场景，完整经历了需求分析、架构设计、编码实现、自动化验证与性能优化五个阶段，交付了一个功能完备、"
     "可靠性经过实测论证的 C 语言 Web 系统。主要成果包括：容量制预约与 FIFO 候补补位的业务闭环；以单写者事务与持久化回执为核心的并发"
     "一致性与幂等设计；覆盖单元、集成、并发、故障注入、模糊与浸泡的五层自动化验证体系；以及从 720 次争抢实验、20 次崩溃恢复实验到"
     "性能优化对比的完整实测证据链。")
h2("7.2 不足与展望")
para("系统当前为单机回环部署，尚未覆盖：传输加密（TLS 证书体系已预留）；跨校区多实例与集中部署；与门禁、一卡通系统的对接；"
     "以及基于历史数据的机时利用率分析与推荐。性能方面，写入路径受 SQLite 单写者模型限制，如需更高写并发可评估分片或更换存储引擎。"
     "这些将作为后续迭代方向。")

# ---------- 参考文献 ----------
h1("参考文献")
refs = [
 "[1] SQLite Consortium. SQLite Documentation: WAL Mode, Transactions[EB/OL]. https://www.sqlite.org/docs.html, 2026.",
 "[2] CivetWeb Project. CivetWeb User Manual[EB/OL]. https://github.com/civetweb/civetweb, 2026.",
 "[3] denisbonvini J., et al. libsodium Documentation[EB/OL]. https://libsodium.org, 2026.",
 "[4] Dave Gamble. cJSON: Ultralightweight JSON parser in ANSI C[EB/OL]. https://github.com/DaveGamble/cJSON, 2026.",
 "[5] 严蔚敏, 吴伟民. 数据结构（C 语言版）[M]. 北京: 清华大学出版社, 2007.",
 "[6] 萨师煊, 王珊. 数据库系统概论（第 5 版）[M]. 北京: 高等教育出版社, 2014.",
 "[7] Abraham Silberschatz. Operating System Concepts (10th Edition)[M]. Wiley, 2018.",
 "[8] ISO/IEC. ISO/IEC 9899:2018 Programming languages — C[S]. 2018.",
 "[9] Gerard J. Holzmann. The Power of Ten: Rules for Developing Safety-Critical Code[J]. Computer, 2006, 39(6): 95-99.",
 "[10] 张海藩. 软件工程导论（第 6 版）[M]. 北京: 清华大学出版社, 2013.",
 "[11] ThrowTheSwitch. Unity Test Framework[EB/OL]. https://github.com/ThrowTheSwitch/Unity, 2026.",
 "[12] GB/T 7714-2015 信息与文献 参考文献著录规则[S]. 北京: 中国标准出版社, 2015.",
]
for r in refs: para(r, indent=False, size=10.5)

# ---------- 致谢 ----------
h1("致  谢")
para("感谢指导教师在选题、系统设计与论文撰写过程中的悉心指导；感谢实验室与同学们在需求调研与系统试用中提出的宝贵意见；"
     "感谢开源社区提供的优秀组件（CivetWeb、SQLite、cJSON、libsodium、Unity），它们是本系统能够以轻量形态达成工程目标的坚实基础。")

# ---------- 附录 ----------
h1("附录 A  接口契约摘要")
para("系统提供 16 个 REST 接口，统一响应封套 {code, message, data}，错误码包括 400/401/403/404/409/413/429/500/503。"
     "完整契约见仓库 docs/CONTRACT.md，主要内容如下：")
tbl(["类别", "端点示例", "说明"],
    [["查询", "GET /api/labs, /api/slots, /api/me/records", "场次/记录查询，支持分页"],
     ["预约", "POST /api/reservations, …/cancel, /checkin", "携带 UUID 请求编号，幂等"],
     ["候补", "POST /api/waitlist, …/withdraw", "满员排队，FIFO 补位"],
     ["账号", "POST /api/me/password, /api/me/sessions/{id}/revoke", "改密踢除其他会话"],
     ["通知", "GET /api/me/notifications, POST …/read", "未读计数与批量已读"],
     ["管理", "POST /api/admin/labs, /slots/publish；GET /admin/stats|metrics|export", "发布、统计、导出、指标"]])
h1("附录 B  运行说明")
para("环境：Windows x64，MinGW-w64 GCC，PowerShell；测试另需 Python 3.9+。构建：powershell -File scripts/build.ps1；"
     "一键演示：powershell -File scripts/start-demo.ps1 -Password 'Demo-Lab-2026'，浏览器访问 http://127.0.0.1:8080，"
     "预置账号 admin 与 user01–user20。自动化测试命令见 tests/README.md。")

doc.save(str(pathlib.Path(__file__).resolve().parent / "毕业论文初稿.docx"))
print("docx 已生成")
