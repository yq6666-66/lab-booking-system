# -*- coding: utf-8 -*-
"""毕业论文初稿生成：docs/thesis/毕业论文初稿.docx（通用学术格式，学校模板后补可套排）。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import pathlib

# ---------- 学校模板到位后的唯一改样处：正文字体/字号/行距/边距/标题级别字号 ----------
STYLE = {
    "body_font": "宋体", "body_size": 12,      # 正文：宋体小四
    "h1_font": "黑体", "h1_size": 16,          # 一级标题：黑体三号
    "h2_font": "黑体", "h2_size": 14,          # 二级标题：黑体四号
    "h3_font": "黑体", "h3_size": 12,          # 三级标题：黑体小四
    "line_spacing": 1.5,                        # 行距
    "indent_chars": 24,                         # 首行缩进（Pt，24pt=小四两字符）
    "margins": {"top": 2.54, "bottom": 2.54, "left": 3.0, "right": 3.0},  # 页边距 cm
}
FIG = pathlib.Path(__file__).resolve().parent / "figures"
doc = Document()

# ---------- 样式 ----------
def set_font(style, name, size, bold=False):
    style.font.name = name; style.font.size = Pt(size); style.font.bold = bold
    style.element.get_or_add_rPr()
    rf = style.element.rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), name)
for sec in doc.sections:
    sec.top_margin = Cm(STYLE["margins"]["top"]); sec.bottom_margin = Cm(STYLE["margins"]["bottom"])
    sec.left_margin = Cm(STYLE["margins"]["left"]); sec.right_margin = Cm(STYLE["margins"]["right"])
set_font(doc.styles["Normal"], STYLE["body_font"], STYLE["body_size"])
doc.styles["Normal"].paragraph_format.line_spacing = STYLE["line_spacing"]
set_font(doc.styles["Heading 1"], STYLE["h1_font"], STYLE["h1_size"], True)
set_font(doc.styles["Heading 2"], STYLE["h2_font"], STYLE["h2_size"], True)
set_font(doc.styles["Heading 3"], STYLE["h3_font"], STYLE["h3_size"], True)

def h1(t):
    p = doc.add_heading(t, level=1); p.paragraph_format.space_before = Pt(18); p.paragraph_format.space_after = Pt(10)
    return p
def h2(t): return doc.add_heading(t, level=2)
def h3(t): return doc.add_heading(t, level=3)
def para(t, indent=True, align=None, size=12, bold=False, font=None):
    p = doc.add_paragraph(); run = p.add_run(t); run.font.size = Pt(size); run.font.bold = bold
    fname = font or STYLE["body_font"]
    run.font.name = fname; run._element.rPr.rFonts.set(qn("w:eastAsia"), STYLE["body_font"])
    if indent: p.paragraph_format.first_line_indent = Pt(STYLE["indent_chars"])
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

def _field(run, instr, placeholder=""):
    """在 run 内插入 Word 域代码（打开文档后按 Ctrl+A → F9 更新）。"""
    b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
    i = OxmlElement("w:instrText"); i.set(qn("xml:space"), "preserve"); i.text = instr
    s = OxmlElement("w:fldChar"); s.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t"); t.text = placeholder
    e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
    for el in (b, i, s, t, e): run._element.append(el)

def add_page_numbers():
    """页脚居中页码（第 X 页）。"""
    p = doc.sections[0].footer.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r0 = p.add_run("第 "); r0.font.size = Pt(9); r0.font.name = "宋体"
    r = p.add_run(); r.font.size = Pt(9); r.font.name = "宋体"
    _field(r, "PAGE", "1")
    r1 = p.add_run(" 页"); r1.font.size = Pt(9); r1.font.name = "宋体"
add_page_numbers()

def add_toc():
    """目录页：插入 TOC 域（覆盖 1–3 级标题）。在 Word 中更新域后自动带页码与超链接。"""
    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("目  录"); r.font.size = Pt(STYLE["h1_size"]); r.font.bold = True
    r.font.name = STYLE["h1_font"]; r._element.rPr.rFonts.set(qn("w:eastAsia"), STYLE["h1_font"])
    p = doc.add_paragraph()
    run = p.add_run()
    _field(run, 'TOC \\o "1-3" \\h \\z \\u', "（在 Word 中：Ctrl+A 全选 → F9 更新域，即可生成目录）")

# ---------- 封面 ----------
for _ in range(4): doc.add_paragraph()
p = para("本科毕业设计（论文）", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=22, bold=True)
doc.add_paragraph()
p = para("基于 C 语言的实验室资源管理与预约候补一体化系统设计与实现", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=18, bold=True)
doc.add_paragraph(); doc.add_paragraph()
for line in ["学    院：＿＿＿＿＿＿＿＿＿＿", "专    业：软件工程", "姓    名：＿＿＿＿＿＿＿＿＿＿",
             "学    号：＿＿＿＿＿＿＿＿＿＿", "指导教师：＿＿＿＿＿＿＿＿＿＿", "完成日期：2026 年 6 月"]:
    para(line, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
doc.add_page_break()

# ---------- 摘要 ----------
h1("摘  要")
para("高校开放实验室普遍存在预约冲突、占而不用、候补无序等问题。本课题设计并实现了一个基于 C 语言的实验室资源管理与预约候补一体化系统，"
     "采用 C11 与 CivetWeb、SQLite、cJSON、libsodium 等嵌入式组件构建浏览器/服务器架构，重点解决三个工程问题：并发争抢下的容量一致性、"
     "重复请求与中断恢复的幂等性、签到爽约与候补补位的业务闭环。")
para("系统以单写者事务（BEGIN IMMEDIATE）配合容量不变量保证并发正确性——占容口径为 CONFIRMED 与 HELD 之和；以持久化请求回执实现同编号同参数重放幂等、同编号异参数冲突拒绝；"
     "以服务端扫描线程在单事务内完成爽约回收与先进先出补位。在此基础上，系统进一步实现了独立管理控制台（场次调整、用户管理、通知发布、"
     "运行日志与资源清单）、限时签到、场次开始提醒、自动备份轮转、爽约信用约束与时间重叠检测等运营能力，并延伸出预约审批流、可执行 FIFO"
     " 候补策略与限时保留（HELD）、高优先级抢占与信用账户、资源维护工单与可用时段窗、资源使用资格授权、API 访问令牌等深化机制，"
     "以全局安全响应头（CSP 等）收紧浏览器攻击面。")
para("系统通过五层自动化验证：24 个单元测试、79 项集成断言组（含 720 次并发争抢实验——实验样本内未出现超卖、30 次进程中断恢复全部正确、"
     "五组跨规则组合验证、凭据生命周期与约束感知建议评估）、56 个端点的契约测试与灰盒/文档测试、模糊稳健性与浸泡稳定性实验，以及持续集成三通道门禁（构建与测试、"
     "静态分析、AddressSanitizer 内存安全）。计时旁路实验表明登录路径对有效与无效账号的响应时间比仅为 1.07，账号枚举的可观测时间差被"
     "显著压平。连接复用与语句缓存优化使读路径吞吐提升最高 541%（约 6.4 倍），p50 延迟下降 87%。")
para("结果表明，在不引入重型运行时的前提下，以规范的事务设计、持久化去重与自动化实验体系，C 语言同样能够构建出具备工业级可靠性论证的"
     "中小型 Web 业务系统。")
para("关键词：实验室预约；候补队列；事务一致性；幂等请求；故障注入；AddressSanitizer；C 语言", bold=True)
doc.add_paragraph()
h1("Abstract")
para("University laboratories commonly suffer from booking conflicts, no-shows and disordered waiting lists. This project designs and "
     "implements a laboratory reservation and waitlist management system in C (C11), built on CivetWeb, SQLite, cJSON and libsodium with a "
     "browser/server architecture. It focuses on three engineering problems: capacity consistency under concurrent contention, idempotency "
     "against duplicated requests and process crashes, and the business loop of check-in, no-show recovery and FIFO promotion.", font="Times New Roman")
para("The system guarantees concurrent correctness with single-writer transactions (BEGIN IMMEDIATE) plus a per-slot capacity invariant "
     "(occupied capacity counts CONFIRMED and HELD); achieves idempotency through persisted request receipts; and completes no-show "
     "reclamation and promotion inside one transaction in a server-side sweeper. Beyond the core loop it provides an approval workflow for "
     "reservations, an executable-FIFO waitlist strategy with time-limited HELD holds, priority-based preemption with credit compensation, "
     "asset maintenance work orders with availability windows and qualification grants, self-managed API access tokens, a standalone admin "
     "console (slot adjustment, user management, announcements and runtime logs), time-windowed check-in, session start reminders, automatic "
     "backup rotation, a no-show credit constraint, time-overlap detection, and hardened browser attack surface via global security headers.", font="Times New Roman")
para("The system passes five layers of automated verification: 24 unit tests, 79 integration assertion groups (including a 720-request "
     "concurrent-contention experiment with no overbooking observed across all trials, 30 crash-recovery trials all correct, five "
     "cross-rule combination checks, differential property tests and admin operation assertions), contract tests over 56 endpoints, gray-box/documentation tests, fuzz and soak stability experiments, "
     "and a four-channel continuous integration gate (build and test, contract/documentation consistency, static analysis, AddressSanitizer memory safety). A timing "
     "side-channel experiment shows the login path responds to valid and invalid accounts within a 1.07x ratio, substantially flattening the "
     "observable timing difference available for account enumeration. Connection reuse and statement caching raise read throughput by up to "
     "541% (about 6.4x) and cut p50 latency by 87%.", font="Times New Roman")
para("The results show that, with disciplined transaction design, persisted deduplication and an automated experiment system, C can deliver "
     "a small-to-medium web application whose reliability is demonstrably verified without heavy runtimes.", font="Times New Roman")
para("Key words: laboratory reservation; waitlist queue; transactional consistency; idempotent request; fault injection; AddressSanitizer; C language", bold=True, font="Times New Roman")
doc.add_page_break()

# ---------- 目录 ----------
add_toc()
doc.add_page_break()

# ---------- 第1章 ----------
h1("1 绪论")
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
li("（1）设计并实现了完整的实验室预约业务闭环：容量制场次查询与预约、可配置策略的候补队列（可执行 FIFO / strict）、取消自动补位、限时签到与爽约回收、预约审批流、站内通知、统计导出；")
li("（2）设计并实现了两项关键机制：基于持久化请求回执的幂等去重（同编号同参数重放、同编号异参数冲突）、以及单写者事务内的取消—补位原子操作；")
li("（3）设计并实现了面向真实运营的管理能力：独立管理员控制台（实验室与场次维护、场次开始前调整、用户停用/重置密码、全员通知发布、运行日志查看）、实验室资源（设备）清单与资源利用率统计、"
   "资源维护工单与可用时段窗、资源使用资格授权、场次开始提醒、自动备份轮转、爽约信用约束（近 7 天两次爽约暂停预约）、跨场次时间重叠检测、"
   "信用账户（补偿、发放与每周回补）、高优先级抢占与 API 访问令牌；")
li("（4）构建了五层自动化验证体系：单元测试 24 个、集成断言组 79 项（T01–T78 与 CLI 检查，含 720 次并发争抢、30 次故障注入恢复、五组跨规则组合验证、凭据生命周期与约束感知建议评估）、56 个端点的契约测试与灰盒/文档测试、模糊与浸泡实验，"
   "并以持续集成三通道门禁（构建与测试、静态分析、AddressSanitizer 内存安全）保障每一次提交；")
li("（5）完成性能与安全实验：每线程连接复用与语句缓存使读路径吞吐最高提升 541%；计时旁路实验将登录路径有效与无效账号的响应时间比压至 1.07，显著降低了账号枚举的可观测差异。")
h2("1.4 主要创新点")
para("结合同类系统的功能对比与本课题的实现约束，本工作的主要创新点归纳为三条：")
li("（1）单写者事务下的规则组合语义工程化：将容量、审批、候补、抢占、信用、资源维护、周配额等多条业务规则的交叉作用显式归纳为五条组合语义基准（契约文档「语义澄清」节），并以五组跨规则组合断言钉死唯一正确答案——例如“待审批预约不占容量但批准时重校”“候补递补作为系统行为不经过审批”，解决了规则叠加场景下系统行为不可预期的工程难题；")
li("（2）可执行候补策略与老化加权：突破传统 FIFO 的队首阻塞，递补事务按（优先级降序，编号升序）扫描并暂跳暂时不可执行的候选、保留其序号，配合限时保留（HELD）与优先级抢占/信用补偿闭环；进一步把操作系统调度的老化思想迁移为加权评分档（优先级 1000/信用 200/等待对数 60 的量纲设计），并以 Jain 公平指数与 SQL 口径等待时长构成三策略×四档负载的量化评估——对照实验显示严格 FIFO 在队首冲突时空置率为 100%，可执行与加权档均能满额递补，且加权档把名额交给高信用短等待者使平均等待时长减半；")
li("（3）约束感知替代建议：以只读端点按申请人当前的配额、信用、时间冲突与场次余位，逐场次解释“可预约/可候补”及不可行原因（满员、配额、冲突等），将候补决策从盲目排队升级为知情选择，且评估与提交分离，不削弱事务内全量校验的正确性兜底。")
h2("1.5 论文组织结构")
para("第 1 章绪论；第 2 章介绍相关技术；第 3 章进行需求分析；第 4 章阐述系统设计；第 5 章说明系统实现；第 6 章给出测试与实验结果；"
     "第 7 章总结全文并展望后续工作。")

# ---------- 第2章 ----------
h1("2 相关技术")
h2("2.1 C11 与 MinGW-w64")
para("系统采用 C11 标准与 MinGW-w64 GCC 15.2 工具链构建，使用 _Thread_local 线程局部存储实现每工作线程的数据库连接复用，"
     "并以 SRWLOCK（读写锁）保护进程内共享状态。构建脚本提供正式版、故障注入测试版（TEST_FAULTS 宏）、加固版（_FORTIFY_SOURCE=3、"
     "栈保护、自动变量零初始化）与覆盖率版四种目标。")
h2("2.2 CivetWeb 嵌入式 HTTP 服务器")
para("CivetWeb 是衍生自 Mongoose 的开源嵌入式 Web 服务器，以单线程库形态嵌入宿主程序，提供多工作线程、静态文件服务、Cookie 解析与"
     "SSL 能力。系统以 NO_SSL 宏裁剪 TLS（传输安全留作后续扩展），固定 8 个工作线程并仅监听回环地址，符合校园单机部署场景。"
     "全局安全响应头（Content-Security-Policy、X-Content-Type-Options、X-Frame-Options、Referrer-Policy）通过 CivetWeb 的 "
     "additional_header 配置统一下发至包括静态页在内的全部响应；静态文件缓存协商由 static_file_cache_control 选项控制，"
     "保证前端资源更新即时生效。")
h2("2.3 SQLite 与事务模型")
para("SQLite 是无服务器的嵌入式关系数据库。系统启用 WAL（写前日志）模式实现读写并行，synchronous=FULL 保证掉电持久性；"
     "所有业务写操作置于 BEGIN IMMEDIATE 短事务中——该模式在事务开始时即获取写锁，将并发冲突转化为串行化排队，是本系统并发正确性的基石。"
     "局部唯一索引（部分索引）用于声明数据不变量；Online Backup API 用于在线备份。")
h2("2.4 cJSON 与 libsodium")
para("cJSON 提供标准 C 的 JSON 解析与序列化，其解析深度限制可抵御嵌套嵌套攻击；libsodium 提供密码学安全的随机数生成与 Argon2id"
     "口令哈希（crypto_pwhash），避免自研密码学带来的风险。")
h2("2.5 前端技术")
para("前端采用无框架的原生 HTML/CSS/JavaScript 单页实现（v1.16.0 引入共用基座 ui.js 与深色主题 theme.js），与嵌入式后端在依赖哲学上保持一致：全部资源本地静态服务，无构建步骤、"
     "无第三方运行时，页面总量不足 60 KB。")

# ---------- 第3章 ----------
h1("3 需求分析")
h2("3.1 角色与用例")
para("系统包含两类角色。普通用户：自助注册与登录、查询开放场次、预约与取消（含改期与备注）、加入/退出候补、确认限时保留名额、在签到窗口内签到与签退、"
     "接收站内通知（补位、爽约、管理员公告、场次提醒）并批量已读、自助修改密码、管理本人在线会话与 API 访问令牌、查看信用余额与流水、按状态筛选与翻页查看本人记录；"
     "管理员：维护实验室及其资源（设备）清单、开启实验室预约审批并处理待审批申请、维护资源的维护工单与可用时段窗、授予/撤销资源使用资格、按日期区间与容量发布场次、"
     "对已发布场次在开始前调整时间与容量、查看全体记录（按动作与用户筛选）与操作日志、管理用户（停用/启用、重置密码、发放信用）、向全员或指定用户"
     "发布公告、查看通知送达与已读情况、查看逐日统计（图表与 CSV 导出）与运行指标、查看服务运行日志。管理员账号从登录页的管理员入口进入，"
     "认证通过后自动跳转独立管理控制台。普通用户可自助注册（角色恒为 USER），管理账号由系统预置。")
h2("3.2 功能需求")
para("核心功能需求归纳为：场次查询（按实验室与日期，返回容量与实时占用）；预约（容量内即时生效；审批实验室落 PENDING 待批）；候补（满员排队，"
     "可执行 FIFO 或 strict 策略排队与递补）；取消与自动补位（释放与补位原子完成）；替代时段提示（满员时返回同实验室 7 天内空闲场次）；限时签到与"
     "爽约回收（超时自动释放并补位）；候补递补限时保留与确认（HELD + confirm）；高优先级抢占（未签到者，信用补偿）；预约改期（同实验室原子改期，旧槽自动补位）与预约备注；跨时段批量连场预约（原子）；预约时资源需求声明与同时段配额校验（BR12，资源随取消/爽约自动释放）；"
     "场次开始提醒（开始前可配置时段内向有效预约者推送站内提醒）；站内通知（补位、爽约、公告、提醒四类，分页加载）；账号自助注册与管理、"
     "API 访问令牌自助签发与吊销、信用余额与流水查询；管理员用户管理（停用立即下线、重置密码一次性展示、信用发放）与审批处理；记录分页与状态筛选；逐日统计图表与 CSV 导出；服务运行日志查看与级别筛选；实验室资源清单维护（名称/规格/数量/可用状态、维护工单、可用时段窗、资格授权）与按实验室的资源利用率与实机时统计导出、周期性场次发布、每周预约配额（BR13）、iCalendar 日历导出。")
h2("3.3 业务规则")
para("系统的业务不变量归纳为以下规则（BR）：")
li("BR1 容量约束：任一场次同一时刻的有效预约数不超过其容量 capacity（1..200）；")
li("BR2 唯一参与：同一用户对同一场次至多持有一条有效预约或一条有效候补；")
li("BR3 FIFO 补位：名额释放与候补补位在同一事务内按候补编号递增顺序连续进行，直至满员或队列空；")
li("BR4 幂等去重：每次写操作携带全局唯一的请求编号；同编号同参数重放返回已保存原结果，同编号异参数返回冲突；失败（5xx）不落回执，可安全重试；")
li("BR5 签到窗口：场次开始后进入签到窗口（可配置），仅本人可签到；窗口过后不可补签，由扫描线程自动爽约回收；")
li("BR6 爽约语义：爽约回收与候补补位在同一事务内完成，并向预约人与候补人各写入一条通知；补位预约以其补位时刻为签到起点；")
li("BR7 权限边界：预约、候补、签到、改密仅限本人；管理接口仅限管理员；用户停用后全部会话立即失效且无法登录；")
li("BR8 防爆破与限流：登录连续失败达到阈值后按用户名锁定；已登录用户的全部写操作受令牌桶限速；")
li("BR9 历史冻结：场次开始后禁止除签到外的一切写操作，历史记录与历史重放不受影响；")
li("BR10 爽约信用：用户近 7 天内爽约达到 2 次进入受限状态，期间拒绝新的预约与候补，窗口滑动后自动恢复；")
li("BR11 时间重叠：同一用户的有效预约在时间轴上不得重叠（跨场次按 [start_at, end_at) 区间相交判定），候补不受此限；")
li("BR12 资源时段配额：预约可声明所需资源（至多 5 项），服务在事务内校验资源归属、可用性与同时段声明数小于资源总量，声明与预约同事务落库，取消/爽约后自动失效；")
li("BR13 每周配额：每用户本周（北京周一 0 点起）有效预约数受可配置上限约束，超限返回 WEEKLY_QUOTA，跨周自动重置；候补不入配额；")
li("BR14 预约提前量：开始前不足提前量（--lead-time，默认关闭）的场次停止受理预约与候补，防止临开始抢占；")
li("BR15 候补优先级与抢占：候补与抢占的优先级由服务端按角色裁定（普通用户 0、管理员 10，不接受客户端自报）；满员时高优先级用户可直接预约并抢占未签到、优先级更低的有效预约，被抢占者获得信用补偿并收到通知；")
li("BR16 信用账户：用户持有信用余额（基准 5，上限 5），签到与被抢占补偿 +1、爽约额外 −1、管理员可发放；余额为零时拒绝新的预约与候补；每周回补至基准（只补不扣），全部变动记录流水；")
li("BR17 资源可用时段窗：可为资源配置按星期与时刻的可用窗口，窗口外的资源声明被拒绝；")
li("BR18 资源维护工单：开启工单即置资源为维修中（重复开启拒绝），关闭后恢复可用；维修中资源拒绝新的声明（既有声明不受影响）；工单开/关时自动通知该资源全部未来有效声明的持有者（按场次逐条站内通知，响应附受影响计数）；")
li("BR19 候补递补三档策略：默认可执行 FIFO——按（优先级降序,编号升序）扫描队列，停用者跳过、临时时间冲突者暂跳并保留原序号，只递补第一个当前可执行的候选；strict 回退为队首阻塞语义；weighted（老化加权）按 score = 1000×优先级 + 200×(信用−5) + 60×log₂(1+等待小时数) 降序扫描——等待每翻倍 +60 分、同档 1 点信用差约需 8 小时等待追平、管理员不被老化越级，冲突暂跳语义与前两档一致；")
li("BR20 限时保留（HELD）：--hold-window 启用后候补递补先落 HELD 态并写入保留截止（min(当前+窗口, 开场)），用户在截止前确认才转 CONFIRMED，超时由扫描器回收为 EXPIRED 并重新递补；占容口径为 CONFIRMED + HELD；")
li("BR21 资格授权：开启资格要求的资源仅对持有效资格的用户开放声明，未持资格返回 QUALIFICATION_REQUIRED；资格由管理员授予与撤销。")
para("多条规则交叉作用时的组合语义以实现契约的「语义澄清」节为唯一基准：容量口径仅计 CONFIRMED 与 HELD（PENDING 为待审批意向不占容量，"
     "批准事务内重校、满则 409）；抢占仅限未签到且优先级更低者；候补递补是系统行为、不经过审批（审批实验室的递补同样直接落 CONFIRMED/HELD）；"
     "资源转维修中后新声明被拒而既有声明保留；改期不重计每周配额（本周有效预约总数守恒）。第五章实现与第六章测试（T63–T67 组合验证）均以此为基准。", indent=True)
h2("3.4 非功能需求")
para("并发正确性（争抢下不超卖）、可用性（进程异常退出后数据一致、可恢复）、安全性（口令哈希、会话与请求伪造防护、防爆破、无账号枚举"
     "侧信道、CSP 等安全响应头）、可观测性（分级日志、访问日志、运行指标、日志查看接口）、可维护性（接口契约文档、schema 幂等迁移、"
     "五层自动化测试与三通道 CI 门禁）、可运营性（演示数据一键生成、自动备份与轮转）。")

# ---------- 第4章 ----------
h1("4 系统设计")
h2("4.1 总体架构")
para("系统采用五层架构，如图 4-1 所示：浏览器层负责交互；接入层由 CivetWeb 提供 HTTP 解析与多线程调度，并完成会话认证与请求伪造防护；"
     "业务层以单写者事务组织全部写路径；数据访问层封装参数绑定、语句缓存、连接复用与 schema 迁移；存储层为启用 WAL 的 SQLite。"
     "横切模块包括限流防爆破、运行指标与结构化日志。")
img("fig4-1-architecture.png", cap_text="图 4-1 系统总体架构")
para("安全边界方面，服务仅监听 127.0.0.1 回环地址，供本机浏览器访问；全部响应携带统一安全响应头（X-Frame-Options: DENY、"
     "Content-Security-Policy: default-src 'self'、Referrer-Policy: no-referrer）；请求体上限 16 KB 且拒绝重复键。")
h2("4.2 数据库设计")
para("数据模型共 17 张表（当前 schema user_version=5），如图 4-2 所示，其中核心 11 张承载预约主流程：reservations（预约）与 waitlist（候补）是核心业务表；request_receipts（请求回执）支撑幂等去重；"
     "operation_events（操作事件）提供审计；notifications（通知）承载补位、爽约、公告与提醒四类站内消息；assets（r13 引入）承载各实验室的"
     "资源（设备）清单，以（实验室,资源名）唯一约束与三态状态（可用/维修中/停用）支撑资源管理。r17–r24 以幂等迁移陆续扩展 6 张表："
     "asset_claims（资源声明，(预约,资源) 复合主键）、asset_windows（可用时段窗）、asset_maintenance（维护工单）、qualifications（资源资格）、"
     "credit_ledger（信用流水）与 api_tokens（API 访问令牌，仅存哈希），并以列追加方式为 reservations 补充 note、checked_out_at、hold_deadline 等。schema 采用版本化幂等迁移："
     "v2 追加签到与取消原因列及通知表；v3 为场次表追加 capacity 列并退役单占用唯一索引；v4 为场次表追加提醒防重列 reminded_at，并以"
     "“建新表—拷贝—改名”方式重建 CHECK 约束变化的通知表；后续扩展全部只追加、可重入，并以构造旧版本数据库的方式实测升级路径。")
img("fig4-2-er.png", cap_text="图 4-2 数据库 ER 图（核心 11 表；schema v5 共 17 表）")
h2("4.3 状态转换设计")
para("预约状态机包含五个状态：PENDING（待审批意向，不占容量）、CONFIRMED（有效预约，占容）、HELD（候补递补的限时保留，占容）、"
     "CANCELLED（终态）与 EXPIRED（保留超时回收）；签到与签退不产生新状态。CANCELLED 的取消原因五分为 USER（主动取消）、NO_SHOW（爽约）、"
     "REJECTED（审批拒绝）、PREEMPTED（被高优先级抢占）与 ADMIN（管理员强制取消）。候补状态机包含 WAITING、PROMOTED、WITHDRAWN、SKIPPED 四个状态（SKIPPED 用于"
     "账号停用等不可递补场景）。全部转换由显式业务动作或扫描线程触发；占容口径统一为 CONFIRMED 与 HELD 之和，PENDING 与 WAITING "
     "不占容量，如图 4-3 所示。")
img("fig4-3-states.png", cap_text="图 4-3 预约与候补状态转换图")
h2("4.4 关键机制设计")
h3("4.4.1 并发一致性：单写者事务与容量不变量")
para("v1 版本曾以部分唯一索引（每场次至多一条 CONFIRMED）作为并发防线的最后兜底。引入容量制后，唯一索引无法表达\u201c至多 capacity 条\u201d"
     "的约束，系统改为完全依赖 BEGIN IMMEDIATE 单写者串行化：同一时刻至多一个写事务，事务内先读取已约数再校验容量，因此容量不变量"
     "\u201c已约数 ≤ capacity\u201d在任意提交序列下均成立。该设计将正确性从\u201c数据库约束兜底\u201d演进为\u201c事务协议保证\u201d，"
     "并在第六章以 720 次并发争抢实验与超容量落库检查加以验证。")
h3("4.4.2 请求去重：持久化回执")
para("预约生命周期形成创建—改期—取消三件套：改期在单事务内完成新槽全量校验、原子迁移与旧槽候补补位（对标 Cal.com 的 reschedule 能力）。客户端为每个写操作生成一次 UUID 请求编号。服务端将（用户，编号）→（动作，参数摘要，HTTP 状态，结果 JSON）作为回执与业务变更在同一"
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
     "的全部会话立即失效。登录接口支持角色提示（role_hint）：以管理员身份登录普通登录入口将返回 403 角色不匹配并引导至管理入口，"
     "此类请求不计入失败锁定计数；对不存在的用户名，服务端仍执行一次等价口令哈希校验（哑验证），消除有效与无效账号的响应时间差，"
     "杜绝账号枚举侧信道（实验见 6.8 节）。")
h3("4.4.5 后台任务：提醒、备份与归档")
para("扫描线程除爽约回收外还承担三类周期任务，均以可配置间隔触发并可整体关闭：（1）场次开始提醒——发现开始时间落在提醒窗口内、"
     "且尚未提醒过的有效场次，向其全部有效预约者写入 REMIND 通知，并以场次表新增的 reminded_at 列标记防重，保证提醒恰好送达一次；"
     "（2）自动备份——以 SQLite Online Backup API 在线导出数据库快照，文件名携带时间戳，按保留数量轮转删除最旧副本（轮转逻辑独立成函数，"
     "可单元测试）；（3）历史归档——定期清理超过保留期的候补记录与请求回执，防止回执表随时间无限膨胀，同时不影响近期的幂等重放。"
     "场次发布另支持星期掩码（仅发布指定星期，对标真实排课）；资源利用率同时提供场次口径与实机时口径——实机时按签到—签退时长聚合，反映真实资源占用。")
h3("4.4.6 业务约束：爽约信用与时间重叠")
para("爽约信用：预约与候补入口在事务内统计申请人近 7 天（滑动窗口）的 NO_SHOW 次数，达到阈值即返回受限错误，窗口滑动后自然恢复，"
     "不引入额外的状态字段。时间重叠：预约时以区间相交谓词（start_at < 已有.end_at AND 已有.start_at < end_at）检查该用户全部有效预约，"
     "命中即拒绝；该检查与容量检查同处单写者事务内，因此不会出现检查与写入之间的竞态窗口。两项约束均在数据库完整性检查中配备对应的"
     "脏数据发现语句，可被单元测试直接验证。")
h3("4.4.7 候补策略、审批流与信用账户（r19–r24）")
para("候补策略：三档可插拔——默认可执行 FIFO（递补事务按（优先级降序，编号升序）扫描队列，账号停用者置 SKIPPED，临时时间冲突者暂跳并保留原序号），"
     "只递补第一个当前可执行的候选，避免队首暂时不可执行时阻塞整条队列；--waitlist-strategy=strict 可回退为队首阻塞语义；weighted 档把操作系统调度的老化（aging）思想迁移到候补队列——score = 1000×优先级 + 200×(信用−5) + 60×log₂(1+等待小时数)，等待时长按对数加速补偿以消除同档饿死，同时信用与角色优先级的量纲设计（1 点信用 ≈ 8 小时等待、管理员 1000 分不被越级）保证激励结构不被老化侵蚀。三档策略共用同一套可执行性扫描，实测对照见 6.7 节。"
     "限时保留：--hold-window 启用后，递补先落 HELD 并写入保留截止（当前时刻+窗口与开场时刻取小），用户在截止前调用确认接口才转 "
     "CONFIRMED；扫描器将过期的 HELD 回收为 EXPIRED 并立即对同一场次重新递补，形成“超时即让位”的重试环。")
para("预约审批流：实验室可开启 require_approval，此后用户主动预约先落 PENDING（不占容量）；管理员批准时在同一事务内重校容量与资源配额，"
     "已满则 409 APPROVAL_CAPACITY，拒绝则 CANCELLED/REJECTED 并双向通知。候补递补被明确定义为系统行为、不经过审批——审批实验室的"
     "递补同样直接落 CONFIRMED（或 HELD），保证名额不因审批流程空置。")
para("抢占与信用账户：满员时高优先级角色（管理员）可直接预约，系统在同一事务内选择“未签到且优先级更低”的有效预约者（CONFIRMED/HELD 中 "
     "priority 最低者）置 CANCELLED/PREEMPTED，补偿 1 点信用并通知；已签到者永不被抢占。信用账户以 credit_ledger 记录全部变动"
     "（签到/补偿 +1、爽约额外 −1、管理员发放、每周回补至基准 5），余额钳制在 0..5，余额为零拒绝新的预约与候补。资源域配套提供维护工单"
     "（开启即维修中、关闭恢复）、按星期与时刻的可用时段窗、以及按资源粒度的使用资格授权，三项校验均与资源声明同事务完成。")
para("在以上规则之上，系统提供只读的约束感知建议端点：对每一场次按当前账号的配额、信用、时间冲突、提前量与余位状态评估“可预约/可候补”并给出原因解释（满员引导候补、周配额不阻塞候补等）；评估与提交之间允许竞态，提交仍由事务内全量校验兜底。", indent=True)

# ---------- 第5章 ----------
h1("5 系统实现")
h2("5.1 开发环境与构建")
para("开发环境为 Windows x64 + MinGW-w64 GCC 15.2，核心代码约 1,600 行 C（不含第三方库）与 260 行前端脚本，自动化测试约 3,400 行。"
     "构建脚本支持四个目标：正式版（自动运行 24 个单元测试）、TEST_FAULTS 测试版（内置三类故障注入点：提交前崩溃 86、提交后响应前崩溃 87、"
     "扫描事务中途崩溃 88）、加固版（FORTIFY、栈保护与自动变量零初始化）与覆盖率版（--coverage，配合优雅停机信号 SIGBREAK 刷写 gcov 数据）。"
     "命令行支持提醒窗口（--remind-sec）、备份间隔（--backup-interval）与演示数据（--demo-days）等运行参数，全部参数均有边界校验。")
h2("5.2 HTTP 接入与会话安全")
para("api() 处理器依次执行 Host 白名单、方法检查、健康探测、Origin 校验、请求体上限与 JSON 合法性检查（含重复键拒绝）、数据库连接获取、"
     "会话认证与 CSRF 校验后才进入业务分发。登录接口前置防爆破闸门：同一用户名连续失败达到阈值（默认 5 次）后锁定 900 秒，期间正确口令"
     "同样返回 429；已登录用户的全部写操作经过每用户令牌桶（默认突发 30、每秒补充 1）限速。路由计数口径：分派器共 67 个路由分支"
     "（其中 26 个带路径参数），另有 Prometheus 抓取端点 /metrics 为独立处理器；契约测试覆盖其中 56 个端点的响应封套与字段类型"
     "（计数以仓库 tests/contract_test.py 的 SCHEMAS 键数为准）。管理端点集中做角色断言，静态资源与 API 共享全局安全响应头。")
h2("5.3 事务与去重核心")
para("booking() 是全部写操作的汇聚点，其主干为：BEGIN IMMEDIATE → 用户可用性检查（含爽约信用受限与时间重叠检测）→ 回执查询（命中即重放/"
     "冲突）→ 目标与状态校验 → 容量校验 → 业务写入与补位 → 回执持久化 → COMMIT；任何一步出错则整体 ROLLBACK 并将临时错误映射为 503。"
     "预约请求可携带资源声明，事务内完成“归属校验 → 同时段配额（BR12）→ 声明落库”，资源列表纳入请求摘要以保证同编号异参数冲突语义；"
     "候补场景下，promote_fill 循环先按可执行 FIFO 策略将队列补至满员，再处理请求者本人：若请求者恰为队首，其候补先行生效，本次直接预约返回"
     "409 并附替代时段，保证 FIFO 公平性不被绕过。审批实验室的主动预约在事务内先插 CONFIRMED 再改写为 PENDING；管理员批准接口在同一事务内"
     "重校容量（含资源声明配额）后转 CONFIRMED，拒绝则置 CANCELLED/REJECTED。满员时高优先级请求者的预约在同一事务内完成抢占选择"
     "（未签到、priority 最低的 CONFIRMED/HELD 记录）、补偿落账与双向通知，任何一步失败整体回滚。")
h2("5.4 性能优化：连接复用与语句缓存")
para("初版实现每个请求新建 SQLite 连接（open + 两条 PRAGMA + close），并对每条 SQL 重复 prepare。后将其优化为每工作线程复用连接"
     "（_Thread_local，致命错误自动重建）并引入按 SQL 文本的预处理语句 LRU 缓存（每连接上限 32 条，使用后 reset 复用、出错即淘汰）。"
     "优化前后的基准对比见 6.6 节：读路径吞吐最高提升 541%。")
h2("5.5 签到、爽约与提醒实现")
para("签到接口校验本人、CONFIRMED 状态与窗口期，以服务端时间落库，同编号重放与重复签到均返回首次签到时间。扫描线程以可配置间隔触发，"
     "单事务内完成\u201c标记 NO_SHOW → 写通知 → 释放席位 → FIFO 补位 → 写 PROMOTED 通知\u201d全流程，并在提交前留有故障注入点。"
     "补位预约的签到起点取其补位创建时刻，与场次的自然开始时间解耦。签退与签到对称：本人已签到且场次未结束时可签退（幂等），"
     "签退—签到时长聚合为实机时，是资源利用率实机时口径的数据来源。提醒任务复用同一线程循环：命中提醒窗口的场次向全部有效预约者写入 "
     "REMIND 通知并以 reminded_at 防重，重复扫描不会产生重复通知。")
h2("5.6 管理控制台与运营功能实现")
para("管理控制台为独立页面（admin.html + admin.js），以十二个页签组织实验室管理（含预约审批开关）、场次发布、场次管理（开始前调整时间与容量）、全员记录"
     "（动作与用户双维筛选，含 PENDING 待审批，支持批量审批——部分成功语义，容量满条目入 failed[]）、用户管理（前缀搜索、分页、停用/启用、重置密码一次性展示、信用发放、近 7 天爽约计数与受限标记）、统计图表、运行指标、"
     "通知发布（全员或指定用户）、通知历史与运行日志（尾部读取、级别与行数筛选）；资源管理页签覆盖维护工单、可用时段窗与资格授权；全员记录表内置管理员强制操作——「强制取消」（ADMIN 原因+递补+通知）与「代签退」（保实机时口径）。控制台概览如图 5-1 所示，用户管理如图 5-2 所示，"
     "统计图表如图 5-3 所示。资源管理页签维护实验室设备清单（名称/规格/数量/三态状态）；统计页的趋势图由前端以纯 DOM API 生成 SVG 双系列柱状图，不引入任何图表库。统计页还提供按实验室的资源利用率表与 CSV 导出。可观测性方面新增 Prometheus 标准文本抓取端点（/metrics，计数器与延迟直方图累积 bucket），运维生态可直接接入。运营侧实现了自动备份线程"
     "（Backup API 导出 + 按保留数量轮转）、30 天历史归档与演示数据生成器（固定随机种子，可复现地生成过去若干天的场次、预约、签到与"
     "爽约历史，并内置结构性容量保护保证重复执行不破坏不变量）。v1.17.0 为运营侧进一步提供三个聚合端点：/api/admin/dashboard 一次调用返回今日统计、待审批数、候补热点与七日趋势（替代前端串行五次请求）；/api/admin/slots/conflicts 检测同日跨实验室场次的时间重叠（排课避撞）；/api/admin/credit-summary 汇总信用分布五档与受限用户名单。用户侧同步新增个人通知历史 CSV 导出（/api/me/notifications/export，带 BOM 的 Excel 兼容格式，上限 500 条）。")
img("fig5-1-admin-overview.png", cap_text="图 5-1 管理控制台运行概览", width=14.5)
img("fig5-2-admin-users.png", cap_text="图 5-2 用户管理（爽约计数与受限标记）", width=14.5)
img("fig5-3-admin-stats.png", cap_text="图 5-3 逐日统计与 SVG 趋势图", width=14.5)
h2("5.7 分页、统计导出与其他")
para("个人与全体记录支持 page/page_size/has_more 分页与状态筛选；统计按北京日聚合七项计数，CSV 导出带 BOM 与合计行以兼容 Excel 直接打开；"
     "前端以上一轮请求编号持久化（sessionStorage）实现跨刷新的重试提示，全部状态变更按钮在确认结果前保持锁定，取消等不可逆操作采用"
     "两段式页内确认，避免重复提交与误操作。")

# ---------- 第6章 ----------
h1("6 系统测试")
h2("6.1 测试策略与环境")
para("测试体系分五层：（1）Unity 单元测试 24 个，不经 HTTP 直接链接业务层与数据层；（2）集成实验 79 项断言组（编号 T01–T78 另加 CLI 数据库"
     "检查，以测试运行器 record() 调用数为准），以独立 Python 客户端"
     "驱动真实服务进程，覆盖功能、安全、并发、故障恢复、运营能力与跨规则组合语义；（3）契约测试（56 个端点逐项校验响应封套与字段类型）与灰盒/文档一致性测试；"
     "（4）模糊稳健性实验与浸泡稳定性实验；（5）真实浏览器端到端验收。所有实验使用独立临时端口与全新数据库副本，原始证据（数据库、日志、"
     "结果 JSON）完整归档，失败样本不销毁。")
para("持续集成设有四条并行门禁通道：构建与单元/集成测试、静态分析门禁（-fanalyzer 与绑定参数静态核查）、以及 AddressSanitizer + "
     "UndefinedBehaviorSanitizer 内存安全通道。ASan 通道基于 llvm-mingw 工具链解决 MinGW 发行版缺少 sanitizer 运行库的问题，"
     "在真实服务进程上执行核心实验组，任一通道失败即阻断合并。")
h2("6.2 单元测试与覆盖率")
para("单元测试覆盖纯函数（编号/UUID/日期/令牌/分页参数）、数据库不变量（种子形状、超容量检测、脏数据发现、v2→v3 迁移实测、备份轮转）、"
     "业务层直调（预约冲突、替代时段对账、候补 FIFO、签到、通知、会话、改密、爽约信用、时间重叠）、限流纯函数（令牌桶边界与补充数学、"
     "登录锁定到期）以及语句缓存（命中、LRU 淘汰、参数重绑、错误不污染）与线程连接复用（致命错误重建），共 24 个用例。")
para("以 --coverage 构建运行全部单元测试与接口走查后，各源文件行覆盖率见表 6-1（gcov 统计）。")
cap("表 6-1 源文件行覆盖率（单元 + 接口与 CLI 走查，gcov 实测）")
tbl(["源文件", "db.c", "http.c", "log.c", "main.c", "metrics.c", "ratelimit.c", "service.c", "util.c"],
    [["行覆盖率 %", 86.55, 89.94, 84.62, 82.56, 95.24, 86.76, 81.48, 98.08]])
h2("6.3 功能、安全与运营回归")
para("集成实验共 79 项断言组（T01–T78 及 CLI 数据库检查），覆盖登录授权、占用唯一、候补 FIFO 与重入队尾、重放幂等、越权与 CSRF/Origin、"
     "畸形与超长输入、过期场次、外部写锁退避、禁用候补跳过、替代时段对账、统计对账、会话清理、签到窗口、分页参数、CSV 导出、改密会话失效、"
     "混合负载连接复用，以及运营扩展的角色登录、场次调整、通知发布、用户管理、场次提醒、自动备份、爽约信用、时间重叠、历史归档、日志查看、"
     "资源管理、利用率统计、资源声明配额、周期性发布、每周配额、签退、预约提前量与改期、审批流、API 令牌、维护工单、可用时段窗、信用账户、"
     "抢占、批量预约、资格授权、可执行 FIFO 与 HELD。最近一轮全部通过。典型新增加断言组如下：")
li("T31 角色登录：管理员凭证配用户入口返回 403 角色不匹配，不计失败锁定；正确入口直达控制台；")
li("T32 场次调整：开始前修改时间与容量生效，容量不得低于当前有效预约数（409），开始后拒绝调整；")
li("T34 用户管理：停用后该用户全部会话立即 401、重置密码后旧口令失效且新口令仅返回一次、不可停用自己、非管理员 403；")
li("T35 场次提醒：提醒窗口内的场次向全部有效预约者推送 REMIND 通知，重复扫描防重（reminded_at），窗口外不推送；")
li("T36 自动备份：备份间隔触发快照落盘，轮转保留数量不超过上限，最旧副本被删除；")
li("T37 爽约信用：近 7 天两次爽约的用户预约/候补均被拒，窗口滑动后自动恢复；")
li("T38 时间重叠：与本人既有有效预约区间相交的场次返回 TIME_CONFLICT，跨实验室同样生效；")
li("T39 历史归档：超过保留期的候补与回执被清理，近期记录不受影响；")
li("T40 日志查看：级别筛选与行数上限生效，错误日志可被检索；")
li("T41/T42 资源管理与利用率：资源增改、同名 409、越权 403、停用资源对用户隐藏；利用率与独立 SQL 对账一致，CSV 导出可用；")
li("T44 资源声明配额：声明预约、同时段超配额 409 ASSET_QUOTA、取消后配额自动释放、同编号不同资源 REQUEST_ID_CONFLICT、声明次数统计；")
li("T45–T47 周期发布/每周配额/签退：掩码过滤与星期断言；--quota-weekly 第 3 单 409 WEEKLY_QUOTA、跨周重置；签退幂等与实机时聚合对账；")
li("T48/T49 提前量与恢复生命周期：--lead-time 下 30 分钟后场次 409 LEAD_TIME、2 小时后放行；备份→篡改→恢复后 integrity_check=ok 且篡改数据消失；")
li("T50 改期流：同实验室原子改期、旧槽 FIFO 补位、资源声明按新时段重校、跨实验室/重叠/满员拒绝；")
li("T51–T52 审批流与待审批列表：require_approval 实验室预约落 PENDING、批准转 CONFIRMED、拒绝 CANCELLED/REJECTED、容量满拒绝批准（409）、非管理员 403；")
li("T53–T62 运营深化：API 令牌签发/使用/吊销、维护工单开闭与重复拒绝、可用时段窗外拒绝（WINDOW_CONFLICT）、信用流水与发放边界、抢占补偿与通知、批量连场原子性、资格授予/撤销与 QUALIFICATION_REQUIRED、可执行 FIFO 暂跳保留序号、HELD 超时回收重递补；")
li("T63–T67 跨规则组合验证（对应契约「语义澄清」节）：审批×HELD（PENDING 不占容量、批准重校、递补绕审批落 HELD 后确认）、优先级×可执行 FIFO（高优先级冲突者被暂跳、低优先级递补；strict 对照整体停止）、抢占×信用（确定性余额控制下补偿 +1 未触顶并可再预约）、维护×资源声明（新声明 409、既有保留、恢复后可声明）、改期×周配额（本周有效预约总数守恒、改期不放水）。")
li("T74 差分属性测试：weighted 递补与参考模型（评分公式+可执行扫描）在 12 组随机场景（随机等待时长/信用/跨场冲突/停用者/管理员混入）下逐场一致——策略实现的性质级验证；")
li("T75 建议 queue_ahead 数值等价断言：预取计算值与精确口径在四场景下逐值对拍——r30 预取优化的正确性证明；")
li("T76 批量审批部分成功语义：容量满条目入 failed[]（含 APPROVAL_CAPACITY 码）不影响其余条目，整体一个事务；")
li("T77 维护影响通知：开/关工单时受影响声明持有者逐场收到站内通知、响应附 affected 计数、过去场次不计入；")
li("T78 管理员强制操作：force-cancel（ADMIN 原因+FIFO 递补+通知）、force-complete（代签退+幂等）、已签到不可直接取消；")
li("T70–T73 策略与治理：weighted 老化（同档久等者反超 id 序、信用压制、管理员不越级）、转正概率（经验分布与手算一致、样本不足 null）、公平性审计（与 SQL 对账、Jain 手算一致、越权 403）、每日候补上限（409 与日界重置）；")
li("T68–T69 凭据生命周期与约束感知建议：停用/改密/重置密码同事务吊销 API 令牌且旧令牌 401；建议端点逐场次给出可预约/可候补与原因——满员可候补（SLOT_FULL）、周配额满不阻塞候补（BR13）、信用为零全部阻塞。")
h2("6.4 并发争抢实验")
para("并发实验以 1、5、10、20 个独立客户端在线程屏障对齐后同时申请同一空闲场次，每档 20 轮共 720 次请求，结果见表 6-2：每轮成功预约数"
     "与数据库占用数恒为 1，其余请求全部收到明确的 409，未出现重复占用或忙碌失败。")
cap("表 6-2 并发争抢实验结果（每档 20 轮）")
tbl(["并发数", "请求数", "成功预约", "409 冲突", "503 忙碌", "p50 中位 (ms)", "p95 (ms)"],
    [[1, 20, 20, 0, 0, 129.3, 129.3], [5, 100, 20, 80, 0, 76.9, 177.6], [10, 200, 20, 180, 0, 97.3, 280.2], [20, 400, 20, 380, 0, 114.8, 273.7]])
para("该实验同时是容量制正确性的直接证据：容量为 1 的场次在全部 720 次争抢样本中未出现超卖与重复占用，表明在本实验的负载与部署条件下，"
     "单写者事务协议足以替代唯一索引兜底。需要说明的是，该结论是实验证据而非形式化保证，其外推以单写者串行化前提保持成立为条件。")
h2("6.5 故障注入恢复实验")
para("故障注入覆盖三类中断点（各 10 轮，共 30 次）：事务提交前崩溃（exit 86，取消已执行、补位未完成）、事务提交后响应前崩溃（exit 87）、"
     "以及扫描事务中途崩溃（exit 88）。三轮实验的结论一致：未提交事务整体回滚（预约保持 CONFIRMED、候补保持 WAITING）；已提交事务完整"
     "保留（补位与通知不丢失）；重启后以原请求编号重试，首次重试完成剩余业务，重复重放返回同一结果；每轮重启后数据库完整性检查通过。")
h2("6.6 性能优化对比实验")
para("以连接复用与语句缓存为唯一变量的前后对比实验结果见表 6-3：基线取自同一 v1.14.0 代码构建的对照变体（将 HTTP 层改回每请求新建数据库连接，因语句缓存随连接生存，该变体同时关闭两项优化），两臂在同日背靠背、同机同参下测得。读路径收益最大：20 并发吞吐由 304 rps 提升至 1948 rps"
     "（+541%），p50 由 63.9ms 降至 8.2ms；写路径受限于写锁串行化，收益收窄但依然正向（+2%～+63%）。")
cap("表 6-3 优化前后基准对比（v1.14.0 对照变体，同日背靠背，完整数据见 docs/evidence/benchmark/）")
tbl(["实验", "场景", "并发", "基线 rps", "优化 rps", "吞吐提升", "p50 变化"],
    [["E1", "read", 10, 366.0, 1355.5, "+270%", "-80.7%"],
     ["E1", "read", 20, 303.8, 1947.8, "+541%", "-87.1%"],
     ["E2", "same-slot", 10, 138.4, 141.8, "+2%", "-5.4%"],
     ["E2", "diff-slot", 10, 95.0, 138.5, "+46%", "-38.6%"],
     ["E2", "same-slot", 20, 136.6, 203.0, "+49%", "-40.4%"],
     ["E2", "diff-slot", 20, 128.0, 209.0, "+63%", "-56.1%"]])
para("在优化对比之外，另以混合负载实验考察写入密集场景的尾延迟与稳定性：6 个并发客户端在 20 秒内混发预约、取消、候补、审批、改期与读请求共 2,320 次，全程零请求错误、零 5xx（200/400/409 分别为 1,198/381/741，4xx 均为预期内的满员与冲突拒绝）；整体延迟 p50=14.5ms、p95=53.6ms、p99=91.8ms、最大 178.7ms。写前日志在实验窗口内由 0.12MB 增至 2.24MB，属写入集中期的正常表现，未见异常增长。该实验为本机回环环境的实测证据，脚本与报告见 scripts/mixed_load.py 与 docs/evidence/load/。", indent=True)
para("在端点层面另做一次 N+1 查询消除：约束感知建议端点最初对 7 天窗口内每场次分别执行排位与概率查询（每请求约数百条 SQL），改为按（场次×优先级）分组计数与按星期分桶的历史释放两条预取查询后，在 104 场次、40 场满员带候补的负载下吞吐由 118 提升至 179 次/秒（+51%），且行为等价性由建议与概率的既有断言（T69/T71）逐值验证。公平审计端点同样消除了 N+1 查询（每用户 7 个相关子查询改为 6 条 GROUP BY 预聚合）。", indent=True)
h2("6.7 候补策略与冲突检测算法对比实验")
para("候补策略对比：以三档策略 × 四档负载（ρ = 预约请求数 / 总容量 = 0.7、1.0、1.3、1.6，过载递增）的真实服务矩阵实验评估递补效果（每格确定性场景：12 场次×容量 3、随机负载用户、结构化候补三角色——队首带跨场冲突者、低信用次早者、高信用第三者；释放由确定性取消触发）。结果见表 6-4：严格 FIFO 在队首冲突时空置全部释放名额（转正 0）；可执行与加权档均能完成递补，且二者把名额交给不同人——可执行档按入队序给出（等待约 7,200 秒），加权档按评分给出（等待约 3,600 秒，平均等待时长减半，归属高信用短等待者）。Jain 公平指数三档一致（0.73～0.82），表明单名额归属的差异在用户总量层面不放大不公平。")
cap("表 6-4 三策略×四档负载矩阵（节选；完整数据 docs/evidence/experiment/strategy_matrix.md）")
tbl(["策略", "ρ", "释放", "入队", "转正", "平均等待(s)", "Jain"],
    [["strict", "0.7~1.6", "6~7", "3", "0", "—", "0.74~0.82"],
     ["executable", "0.7~1.6", "6~7", "3", "1", "7204~7209", "0.73~0.79"],
     ["weighted", "0.7~1.6", "6~7", "3", "1", "3603~3608", "0.73~0.79"]])
img("fig6-1-strategies.png", cap_text="图 6-1 三策略×四档负载的候补递补对比（左：平均等待时长；右：Jain 公平指数）", width=14.0)
para("冲突检测算法对比：以纯算法模拟（Python 口径）对比朴素区间线扫 O(n) 与一天 48 片时间片位图按位与 O(1)——随机生成 n 条既有预约、各执行一万次目标查询：命中场景（朴素提前返回）两者相当（1.2～1.4 倍）；未命中最坏场景（朴素全扫）差距随 n 线性放大，n=100/1,000/10,000 时分别为 14/153/1,846 倍，实测语义零偏差。该实验用于论证复杂度阶差异（O(n)→O(1)）与可选优化方向；生产路径仍采用 SQLite 索引化区间查询，因其事务内一致性与现有负载下的充分性能余量（见 6.6 节）。证据见 docs/evidence/bitmap/。", indent=True)
h2("6.8 安全、稳健性与稳定性实验")
para("计时旁路实验：以脚本分别测量有效账号与不存在账号的登录响应时间各若干轮，二者中位数的比值稳定在 1.07（加固构建下 1.03）。"
     "哑哈希验证将 Argon2id 仅对有效账号执行所引入的时间差压缩到约 7%（加固构建约 3%）的可观测区间内，在本文实验的样本量与局域网环境下"
     "显著降低了账号枚举的计时侧信道可利用性；受限于响应时间的自然抖动，实验不支持“差异为零”的更强结论。")
para("模糊实验以 4 线程对登录、预约、候补、改密接口持续发送畸形 JSON、超长字段、错类型、二进制垃圾与并发错乱序列共 240 次：全部请求"
     "得到合法封套响应（4xx 为主），进程存活，SQLite 完整性检查通过。浸泡实验以混合负载运行 60 秒冒烟（34,970 请求、0 错误、0 5xx），"
     "进程工作集增长 3 MB、句柄增长 44，在浸泡窗口内未观察到工作集与句柄的持续增长趋势；另将浸泡窗口延长至 300 秒（16,170 请求、0 错误、0 5xx），工作集在预热期峰值 6.7MB 后回落并全程稳定在 0.2MB、句柄净增 54，长窗口同样未观察到持续增长；上述结果支持“无持续泄漏迹象”的判断，"
     "不构成对任意时长运行下无泄漏的证明。")
h2("6.9 调试过程记录")
para("开发过程中依据测试暴露并修复了多个真实缺陷，其现象与修复过程如下：")
li("（1）SQL 绑定格式串错位（四例）：sessions_list 将字符串参数按 'is' 中的 i（8 字节）绑定，/api/slots 查询漏写占位符，演示数据生成器"
     "以四个绑定参数对应三个占位符，资源更新语句直接复制了插入语句的六参数格式串（五占位符）——四种错位分别导致进程段错误、查不到任何场次、"
     "插入静默失败与更新段错误，均以最小化二分或“逐请求+服务存活探测”定位，并在完整性检查中补充逐项原因打印；")
li("（2）变参宽度错位：publish_slots 以 int 形参经变参传递给按 8 字节读取的绑定宏，种子初始化报 CHECK 约束失败——统一改用 Id 传递；")
li("（3）爽约—补位死循环：补位产生的新预约以场次开始时间为签到起点，刚补位即再次被判超时回收——改为以补位时刻为起点；")
li("（4）限流桶初始化哨兵碰撞：以 last_ms==0 兼作\u201c未初始化\u201d标记与合法时间戳 0，t=0 边界单测暴露后改用显式 started 标志；")
li("（5）迁移回填遗漏：历史取消记录缺 cancel_reason 会使完整性检查失败——迁移中统一回填为 USER，并以构造 v1/v2 旧库实测升级；")
li("（6）固定种子演示数据的伪幂等：演示数据生成器以固定随机种子实现\u201c重复执行结果一致\u201d，但其前提是随机数消耗序列不变；"
     "增删分支代码后序列漂移，重复执行叠加插入导致超容——改为在插入前检查场次有效预约数（结构性容量保护），使幂等性不再依赖序列对齐；")
li("（7）安全头误伤内联样式：Content-Security-Policy 未显式设置 style-src 时回落 default-src，导致管理页内联 <style> 块被浏览器拦截——"
     "将样式全部迁入外部文件，保持零内联的严格策略。")
li("（8）策略开关下的静默退化（v1.15.0）：weighted 递补分支读取数据库行时沿用了字符串取值辅助函数解析数字列，"
   "该函数仅对文本列有效——优先级、信用与等待时长三个评分项全部取默认值，策略在运行时退化为基础序而测试仍通过"
   "（断言恰好与退化行为兼容）。教训：新增执行路径的测试必须构造与既有默认行为可区分的断言；数字列一律显式按数值类型读取。")
li("（9）SQL 绑定格式串与占位符数量错位（两例）：聚合查询 6 个占位符配 7 个格式字符、排位查询 6 个占位符配 5 个——"
   "前者多读一个变参（读到相邻栈内存），后者末个占位符绑定失败，均表现为接口 500 或间歇性空结果。"
   "与第（1）例的宽度错位合并构成同一类纪律：格式串必须与占位符逐一对齐并纳入评审检查。")
li("（10）整型实参经变参按 8 字节读取（未定义行为）：转正概率的星期参数以 32 位整型传入，而绑定辅助层按 64 位整型读取变参——"
   "高 32 位为调用现场残留，绑定的星期值随机错误，历史统计查询间歇性返回空集。该缺陷在两轮全量回归中偶现后被最小复现定位，"
   "修复为调用处显式宽化。这是项目第 11 例参数传递缺陷，也是\u201c未定义行为不等于立刻崩溃\u201d的真实样本。")
para("上述缺陷均由自动化测试或浏览器验收先行暴露，修复后以新增回归用例固化，验证了\u201c测试驱动发现—最小复现—修复—回归固化\u201d流程的有效性。")
h2("6.10 浏览器端到端验收")
para("以真实 Chromium 浏览器完成八用例端到端验收（含 case8 管理端强制取消按钮 UI→DB 全链）：走通登录（含管理员入口）、查询、预约、候补、取消补位（两段式确认）、签到（含倒计时与"
     "状态机）、预约审批（PENDING 徽标与管理端批准）、资源声明、通知已读与分页、记录状态筛选、改密与会话下线、API 令牌面板、分页、统计图表与 CSV 下载、容量显示与替代时段改约（改期面板）、指标面板，以及管理控制台"
     "全部十二个页签（场次调整、待审批专页、批量审批、强制操作按钮、用户停用与受限标记、通知发布与送达、运行日志筛选等）。截图存证于 docs/evidence/ui-r3/、ui-r5/ 与后续轮次归档。")

# ---------- 第7章 ----------
h1("7 总结与展望")
h2("7.1 工作总结")
para("本课题面向高校开放实验室管理场景，完整经历了需求分析、架构设计、编码实现、自动化验证与性能优化五个阶段，交付了一个功能完备、"
     "可靠性经过实测论证的 C 语言 Web 系统（v1.17.0，schema v5、17 张数据表、56 个契约端点、79 项集成断言组）。主要成果包括：容量制预约与"
     "候补补位的业务闭环，延伸至限时签到、场次提醒、爽约信用、时间重叠检测的完整运营规则，并深化出预约审批流、可执行 FIFO 与限时保留（HELD）"
     "的候补策略、优先级抢占与信用账户、资源维护工单/时段窗/资格授权等一体化机制；以单写者事务与持久化回执为核心的并发一致性与幂等设计；"
     "面向真实管理的独立控制台与自动备份、历史归档等运营能力；覆盖单元、集成、契约、并发、故障注入、模糊与浸泡的五层自动化验证体系，"
     "以及构建、静态分析与 AddressSanitizer 四通道持续集成门禁；从 720 次争抢实验、30 次崩溃恢复实验、跨规则组合验证、计时旁路实验到"
     "性能优化对比的完整实测证据链。")
h2("7.2 不足与展望")
para("系统当前为单机回环部署，尚未覆盖：传输加密（TLS 证书体系已预留）；跨校区多实例与集中部署；与门禁、一卡通系统的对接；"
     "以及基于历史数据的机时利用率分析与推荐。性能方面，写入路径受 SQLite 单写者模型限制，如需更高写并发可评估分片或更换存储引擎。"
     "管理能力方面，通知目前为站内信形态，后续可扩展邮件或即时消息推送。这些将作为后续迭代方向。")

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
para("系统共 67 个路由分支，契约测试覆盖 52 个 REST 端点的响应封套与字段类型，统一封套 {code, message, data}，错误码包括 400/401/403/404/409/413/429/500/503。"
     "完整契约见仓库 docs/CONTRACT.md，主要内容如下：")
tbl(["类别", "端点示例", "说明"],
    [["查询", "GET /api/labs, /api/slots, /api/me/records", "场次/记录查询，支持分页与状态筛选"],
     ["预约", "POST /api/reservations, …/cancel, /checkin, /checkout", "携带 UUID 请求编号，幂等；签到/签退"],
     ["预约", "POST /api/reservations/{id}/reschedule, /batch", "原子改期（旧槽补位）与跨时段连场预约"],
     ["预约", "POST /api/reservations/{id}/approve, /reject, /confirm", "审批批准/拒绝（重校容量）与 HELD 确认"],
     ["候补", "POST /api/waitlist, …/withdraw", "满员排队，可执行 FIFO 递补"],
     ["信用", "GET /api/me/credits; POST /api/admin/users/{id}/credit", "信用余额/流水与管理员发放"],
     ["账号", "POST /api/me/password, /api/me/sessions/{id}/revoke", "改密踢除其他会话"],
     ["令牌", "GET/POST /api/me/tokens; POST …/revoke", "API 访问令牌自助管理（服务端仅存哈希）"],
     ["通知", "GET /api/me/notifications, POST …/read", "未读计数、批量已读与分页加载"],
     ["管理", "POST /api/admin/labs, /slots/publish, /slots/{id}/update", "发布与开始前调整"],
     ["管理", "GET /api/admin/users, POST …/disable|reset-password", "用户管理与一次性口令"],
     ["管理", "POST /api/admin/notifications, GET …/sent", "全员/定向公告与送达情况"],
     ["管理", "GET /api/admin/stats|metrics|export|logs", "统计、指标、导出与运行日志"],
     ["管理", "GET /api/admin/dashboard|slots/conflicts|credit-summary", "聚合概览、跨实验室场次冲突检测与信用健康概览（v1.17.0）"],
     ["通知", "GET /api/me/notifications/export", "个人通知历史 CSV 导出（带 BOM，Excel 兼容）"],
     ["资源", "GET /api/labs/{id}/assets; POST /api/admin/labs/{id}/assets", "资源清单与增改（一体化）"],
     ["资源", "POST /api/admin/assets/{id}/maintenance, /windows", "维护工单与可用时段窗"],
     ["资源", "GET/POST /api/admin/assets/{id}/qualifications", "资源使用资格授予/撤销"],
     ["资源", "GET /api/admin/labs/utilization; …/export", "资源利用率与 CSV 导出"]])
h1("附录 B  运行说明")
para("环境：Windows x64，MinGW-w64 GCC，PowerShell；测试另需 Python 3.9+。构建：powershell -File scripts/build.ps1；"
     "一键演示：powershell -File scripts/start-demo.ps1 -Password 'Demo-Lab-2026'，浏览器访问 http://127.0.0.1:8080，"
     "预置账号 admin 与 user01–user20，登录页分用户与管理员两个入口。演示历史数据可用 --demo-days N 一键生成。"
     "自动化测试命令见 tests/README.md。")

doc.save(str(pathlib.Path(__file__).resolve().parent / "毕业论文初稿.docx"))
print("docx 已生成")
