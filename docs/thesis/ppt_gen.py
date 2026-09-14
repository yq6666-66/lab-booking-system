# -*- coding: utf-8 -*-
"""答辩 PPT 生成：docs/thesis/答辩演示.pptx（16:9，15 页）。口径与论文 v2 同源（r12，v1.3.0）。"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
import pathlib

G = RGBColor(0x17, 0x6D, 0x58); TX = RGBColor(0x18, 0x32, 0x2D); GY = RGBColor(0x6C, 0x7E, 0x75)
FIG = pathlib.Path(__file__).resolve().parent / "figures"
prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

def slide(title, bullets=None, img=None, note=""):
    s = prs.slides.add_slide(BLANK)
    bar = s.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, Inches(0.9))
    bar.fill.solid(); bar.fill.fore_color.rgb = G; bar.line.fill.background()
    tf = bar.text_frame; tf.text = title
    tf.paragraphs[0].font.size = Pt(28); tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    if img:
        p = s.shapes.add_picture(str(FIG / img), Inches(0.8), Inches(1.2), width=Inches(9.2))
        if note:
            nb = s.shapes.add_textbox(Inches(10.2), Inches(1.4), Inches(2.8), Inches(5.5))
            ntf = nb.text_frame; ntf.word_wrap = True
            ntf.text = note; ntf.paragraphs[0].font.size = Pt(14); ntf.paragraphs[0].font.color.rgb = TX
    elif bullets:
        body = s.shapes.add_textbox(Inches(0.9), Inches(1.3), Inches(11.5), Inches(5.6))
        btf = body.text_frame; btf.word_wrap = True
        for i, (t, lvl) in enumerate(bullets):
            para = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
            para.text = ("▪ " if lvl == 0 else "– ") + t if lvl >= 0 else t
            para.level = max(lvl, 0); para.font.size = Pt(24 if lvl == 0 else 19)
            para.font.color.rgb = TX; para.space_after = Pt(10)
    return s

slide("基于 C 语言的实验室资源管理与预约候补一体化系统", bullets=[
    ("软件工程毕业设计答辩", 0), ("答辩人：＿＿＿＿＿＿　指导教师：＿＿＿＿＿＿", 0),
], note="封面")
slide("一、背景与问题", bullets=[
    ("开放实验室机时紧张：冲突、爽约、候补无序", 0),
    ("人工/表格管理：无冲突防护、名额不流转、难审计", 0),
    ("选题价值：并发争抢 + 幂等去重 + 崩溃恢复，是软件工程核心难题的浓缩场景", 0),
    ("语言选择：C11 贴近系统底层，显式资源管理，正确性论证更有说服力", 0),
])
slide("二、系统总览", img="fig4-1-architecture.png", note="五层架构：浏览器 → CivetWeb 接入 → 单写者事务业务层 → db.c 数据访问 → SQLite。横切：限流、指标、日志、后台任务（提醒/备份/归档）。")
slide("三、需求与业务规则（BR1–BR11）", bullets=[
    ("容量制预约：场次 1..200 席位，实时显示已约 X/Y", 0),
    ("FIFO 候补：满员排队，释放后同事务连续补位", 0),
    ("限时签到：超时爽约回收并自动补位，双向通知", 0),
    ("幂等重试：每操作携带 UUID，同号重放返回原结果", 0),
    ("爽约信用：近 7 天两次爽约受限拒约；时间重叠检测", 0),
    ("资源声明：预约时勾选所需设备，同时段配额事务内校验", 0),
    ("管理端：独立控制台——场次调整、用户管理、通知发布、资源清单、运行日志", 0),
])
slide("四、数据模型（schema v2→v6）", img="fig4-2-er.png", note="11 张表；v3 容量制；v4 提醒防重 + CHECK 重建；v5 资源表 assets；v6 资源声明表 asset_claims（预约×资源）。全部幂等迁移，旧库实测。")
slide("五、核心机制 1：并发一致性", bullets=[
    ("BEGIN IMMEDIATE 单写者：写事务串行化排队", 0),
    ("事务内校验容量不变量：已约数 ≤ capacity", 0),
    ("唯一索引兜底（v1）→ 事务协议保证（v3）的演进", 0),
    ("实证：720 次并发争抢，每轮恰好 1 席，零超卖", 0),
])
slide("六、核心机制 2：请求去重回执", img="fig4-6-dedup.png", note="同编号同参数重放原结果；同编号异参数 409；5xx 不落回执可安全重试——以“至少一次+幂等”替代“恰好一次”。")
slide("七、核心机制 3：取消补位与爽约回收", img="fig4-4-cancel-seq.png", note="取消与 promote_fill 同事务；扫描线程单事务完成爽约回收+补位+通知；补位以补位时刻为签到起点，消除死循环。")
slide("八、管理控制台与运营能力", img="fig5-1-admin-overview.png", note="独立控制台十一页签：场次调整、用户管理、资源管理（三态维护）、通知发布、运行日志；统计页含利用率表与 SVG 图。用户端预约卡可勾选资源声明，同时段配额事务内校验（BR12），取消自动释放。运营：提醒、备份轮转、归档、--demo-days。")
slide("九、验证体系：五层自动化", bullets=[
    ("单元测试 24 个（纯函数/不变量/业务直调/限流纯函数）", 0),
    ("集成实验 45 项断言组（独立 Python 客户端驱动真实进程）", 0),
    ("故障注入三类各 10 轮共 30 次：提交前 86 / 提交后 87 / 扫描中途 88，全部正确", 0),
    ("模糊 240 次恶意输入零崩溃；浸泡 34970 请求零错误", 0),
    ("CI 三通道门禁：构建与测试 / 静态分析 / AddressSanitizer 内存安全（llvm-mingw）", 0),
    ("计时旁路实验：登录有效/无效账号响应时间比 1.07，无账号枚举侧信道", 0),
])
slide("十、并发争抢实验", bullets=[
    ("1/5/10/20 并发 × 20 轮 = 720 次争抢", 0),
    ("每轮成功预约数 = 数据库占用数 = 1，零超卖", 0),
    ("其余请求全部收到明确 409，无忙碌失败", 0),
    ("容量制下并发正确性由事务协议完全保证", 0),
])
slide("十一、性能优化实验", bullets=[
    ("优化点：每线程连接复用 + 预编译语句 LRU 缓存", 0),
    ("读路径：20 并发吞吐 451 → 3155 rps（+599%）", 0),
    ("读路径 p50：41.71ms → 5.73ms（-86%）", 0),
    ("写路径受写锁约束：+10%~+100%，符合预期", 0),
    ("方法：同机同参前后对比，数据与脚本全部归档", 0),
])
slide("十二、覆盖率与稳健性", bullets=[
    ("gcov 行覆盖率：全模块 84.6% ~ 98%", 0),
    ("模糊实验 240 次恶意输入：零崩溃、零 5xx", 0),
    ("浸泡 34970 请求：工作集 +3MB、句柄 +44，无泄漏", 0),
    ("七个真实缺陷由测试先行暴露并回归固化", 0),
])
slide("十三、总结与展望", bullets=[
    ("结论：C + 轻组件 + 事务纪律 + 自动化实验 = 可论证的可靠性", 0),
    ("业务闭环延伸至资源管理一体化：资源清单、利用率统计、提醒、备份轮转、归档、爽约信用、重叠检测", 0),
    ("不足：单机回环、无 TLS、写并发受单写者限制", 0),
    ("展望：TLS、门禁对接、多实例、机时分析、推送渠道", 0),
])
slide("恳请各位老师批评指正", bullets=[("演示环境：http://127.0.0.1:8080（现场走查）", 0)], note="致谢页")
prs.save(str(pathlib.Path(__file__).resolve().parent / "答辩演示.pptx"))
print("pptx 已生成：15 页")
