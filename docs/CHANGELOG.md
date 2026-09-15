# 更新日志

本项目遵循语义化版本。所有重要变更记录于此。

## [1.13.0] - 2026-09-16

第二十二轮（第一批）：候补策略升级——可执行 FIFO、HELD 限时保留、可注入时钟。

### 新增
- **可执行 FIFO 候补递补（BR19）**：递补不再只取队首。按 `priority DESC,id` 顺序扫描候补队列，账号停用者置 `SKIPPED`；**临时时间冲突者暂跳并保留原序号**，只递补第一个当前可执行的候选。这样队首暂时不可执行时不会阻塞整条队列、名额不被空置。`--waitlist-strategy=strict` 可回退到旧语义（队首不可执行即阻塞），供对照实验使用（T61）。
- **HELD 限时保留（BR20）**：`--hold-window=N` 启用后，候补递补先落 `HELD` 态并写入 `hold_deadline = min(now+N, 时段开始时刻)`，用户在截止前调用 `POST /api/reservations/{id}/confirm` 才转为 `CONFIRMED`；扫描器把超时的 HELD 转 `EXPIRED` 并**立即对同一场次重新递补**，形成"超时即让位"的重试环（T62）。**默认 `--hold-window=0` 保持原有"补位即确认"行为**。
- **可注入时钟**：新增统一时间源 `clock_now()`（默认系统时间），`--fake-now=<epoch>` 可固定当前时刻，使保留截止等时间边界可确定性测试。
- 新增 `Config` 字段 `hold_window` / `waitlist_strict` / `fake_now` 与对应命令行参数。

### 变更
- `reservations.status` 扩展 `HELD`、`EXPIRED`；新增 `hold_deadline` 列。**占容量口径统一为 `CONFIRMED + HELD`**（否则启用保留后会超售），时间冲突判定同步纳入 HELD。
- schema `user_version` 4 → 5；集成断言 61 → 63；`LAB_VERSION` 1.12.0 → 1.13.0。

### 第二批（资格授权与对照实验）
- **资格授权（BR21）**：`qualifications` 表与 `assets.requires_qualification` 开关；开启后声明该资源须持有有效资格，否则 409 QUALIFICATION_REQUIRED。新增 `GET/POST /api/admin/assets/{id}/qualifications`（grant/revoke）与 `op:"require"` 开关。**默认不要求**，完全向后兼容。
- **对照实验脚本** `tests/experiment.py`：以同一申请序列分别驱动 `--waitlist-strategy=strict|executable` 两臂，统计名额利用率、候补成功率、等待时间与队首阻塞次数。实测（3 轮/臂）：**利用率 0.0 → 1.0**，候补成功率 0.0 → 1.0，队首阻塞 3 次；证据见 `docs/evidence/experiment/fifo_comparison.json`。

### 兼容性
- 默认配置下不产生 HELD，既有 61 项断言语义不变（已实测全绿）；可执行 FIFO 仅在"队首临时冲突"这一既有缺陷场景下改变结果。

## [1.12.0] - 2026-09-16

第二十一轮：候补策略与资源生命周期（优先级/抢占、信用账户、资源时段与维护工单、连续预约、日历导出）。

### 新增
- **候补优先级与抢占（BR15）**：`waitlist.priority` / `reservations.priority` 由服务端按角色校准（管理员 10、普通用户 0），候补出队按 `priority DESC,id`——高优先级内仍保持 FIFO；管理员预约遇满员时可抢占"未签到的最低优先级确认预约"，被抢占者记 `cancel_reason='PREEMPTED'`（不计爽约）、获 1 点信用补偿并收到通知（T56）。
- **信用账户（BR16）**：`users.credit`（钳制 0..5，基准 5）+ `credit_ledger` 流水；签到 +1、爽约 -1、被抢占 +1、每周一自动回补至基准；余额为 0 时禁止新预约与候补。**预约本身不消耗信用**，故不改变既有预约行为。新增 `GET /api/me/credits` 与 `POST /api/admin/users/{id}/credit`（T55）。
- **资源自身可用时段（BR17）**：`asset_windows` 表与 `GET/POST /api/admin/assets/{id}/windows`；未配置的资源视为全天可用（向后兼容），配置后声明该资源的场次必须落在其开放窗口内，否则 409 WINDOW_CONFLICT（T57）。
- **资源维护工单（BR18）**：`asset_maintenance` 表与 `GET/POST /api/admin/assets/{id}/maintenance`（`op=open|close`）；开启即置资源为维修中，关闭恢复可用，重复开启 409（T58）。
- **跨时段连续预约**：`POST /api/reservations/batch {slot_ids[]}`（1..8 个同实验室、时间连续的场次），同一事务内全成或全败，消除"订到一半"（T59）。
- **日历导出**：`GET /api/me/calendar.ics` 输出 ICS 文本，可直接导入日历客户端（T60）。

### 变更
- schema `user_version` 3 → 4（幂等迁移：新增列/新表，并把 `reservations.cancel_reason` 的 CHECK 扩展 `PREEMPTED`）。
- 契约端点 39 → 47；集成断言 55 → 61；`LAB_VERSION` 1.11.0 → 1.12.0。

### 本轮取舍
- **时段模板（可配置节次时长）未实现**：`slots` 的 `CHECK(end_at=start_at+3600)` 是"固定一小时场次"的核心约束，放宽须重建表并改变既有语义与断言，故保留现状。
- 通知渠道插件化、`service.c` 领域拆分未在本轮落地。

## [1.11.0] - 2026-09-15

第二十轮：管理端待审批专页签、声明占用 CSV 导出与 API 令牌只读访问。

### 新增
- 管理端「待审批」专页签：`GET /api/admin/records?status=PENDING` 仅列出 PENDING 预约（不传 `date` 即不按日期过滤，覆盖跨天场次），支持就地批准/拒绝；概览新增「待审批」卡片与页签徽标计数（T52）。
- 声明占用 CSV 导出：`GET /api/admin/asset-claims/export?start_date=&end_date=` 输出带 BOM 的 CSV（资源编号/资源名称/声明次数/最近占用日期 + 合计行），为只读操作（T53）。
- X-API-Token 只读访问：GET 请求可携带 `X-API-Token` 头免 Cookie 认证，命中即刷新 `last_used_at`；写操作、令牌吊销端点与越权端点一律拒绝（T54）。

### 修复
- `/api/admin/records` 新增 `status` 过滤参数；`records()` 原将 status 拼为字面量 `'?'` 致条件恒不成立，现按白名单枚举直接拼接（T52 覆盖）。
- 令牌吊销 `POST /api/me/tokens/{id}/revoke` 原误置于 GET 分支——GET 请求体不被解析、`request_id` 恒缺失，该端点恒返回 400、功能不可用；现按契约移入 POST 分支。
- 管理端「声明占用」表调用未定义的 `stamp()`，致该表始终加载失败，改用 `fmtStamp()`。
- `utilization_export` 表头 `利用率%\n` 的 `%` 未转义（触发 `-Wformat` 告警且表头错乱），改为 `%%`。
- 契约文档与测试 schema 补齐既有漂移字段（health.version/uptime_s、records 的 username/lab_id/note、events.id、tokens.last_used_at、utilization 实机时三列），契约测试实现零漂移。

### 变更
- 契约端点 36 → 39（补入 r21 遗漏的 `asset-claims` 及本轮 `asset-claims/export`）；集成断言 52 → 55。

## [1.10.0] - 2026-09-15

第十九轮：资源声明数据闭环可视化。

### 新增
- 声明占用查询：`GET /api/admin/asset-claims?start_date=&end_date=` 按资源聚合声明次数与最近声明时段；管理端「资源管理」页签新增声明占用表。

## [1.9.0] - 2026-09-15

第十八轮：程序化访问能力（对标 Cal.com API keys）。

### 新增
- API 令牌：POST /api/me/tokens 创建（明文一次性返回，库存哈希）、GET 列表、revoke 吊销（按 id）；令牌可携带于 X-API-Token 头做只读 GET 访问（后续开放写路径）；
- 修复：api_tokens 表结构（补 id 主键）与令牌 INSERT 绑定顺序（sisi）。

## [1.8.0] - 2026-09-15

第十七轮：预约生命周期三件套补全（对标 Cal.com create/reschedule/cancel）。

### 新增
- 改期：`POST /api/reservations/{id}/reschedule`——同实验室原子改期，事务内重校新槽容量/时间重叠(排除自身)/提前量/资源声明配额（声明保留按新时段计入），改期后旧槽自动触发 FIFO 补位，审计 RESCHEDULE（T50）；
- 预约备注：预约请求可选 `note`（≤200 字符），随记录返回并在用户/管理端展示；
- 集成 51 项（T50）、契约 34 端点。

## [1.7.0] - 2026-09-15

第十六轮：可观测性标准化与备份生命周期补全（对标 Prometheus 与成熟备份工具实践）。

### 新增
- Prometheus 抓取端点：`GET /metrics` 标准文本格式 0.0.4（请求/响应计数器 + 延迟直方图累积 bucket/sum/count），服务仅回环监听故不对外暴露；
- 备份恢复命令：`--restore FILE` 从备份灌回主库并自动执行 integrity_check，补完"备份—恢复—验证"生命周期（T49）；
- 预约提前量（BR14）：`--lead-time SEC` 限制开始前不足提前量的场次受理，防止临开始抢占（T48）。

## [1.6.0] - 2026-09-14

第十五轮：对标 LibreBooking/Booked Scheduler 的四项能力深化。

### 新增
- 周期性场次发布：`weekdays` 7 位掩码（仅周一三五等），实验室真实排课场景；
- 每周预约配额（BR13）：`--quota-weekly N` 限本周有效预约数，超限 409 WEEKLY_QUOTA，跨周自动重置；
- 签退与实机时利用率：`POST /api/reservations/{id}/checkout`（幂等），reservations 幂等追加 checked_out_at，利用率新增实机时口径（actual_minutes/utilization_actual）并列展示；
- 我的预约 iCalendar 导出：`GET /api/me/calendar/export`（VEVENT/UTC，兼容日历应用）。
- 明确不做：QR 码签到（无第三方库前提下 C 端 QR 编码成本过高，见论文取舍说明）。

## [1.5.0] - 2026-09-14

第十四轮：资源-预约深度一体化（声明配额机制）。

### 新增
- 资源需求声明：预约请求可携带 `assets` 列表，服务在单写者事务内校验资源归属/可用性与**同时段配额**（BR12：同时段声明数 < 资源总数），声明与预约同事务落库（asset_claims，schema 幂等建表）；
- 配额自动释放：预约取消/爽约后声明自动失效（统计口径仅计有效预约），无需人工清理；
- 幂等强化：资源列表纳入请求摘要，同编号不同资源组合仍返回 REQUEST_ID_CONFLICT；
- 资源使用统计：`GET /api/admin/assets/{id}/usage` 按日聚合声明次数（仅管理员）；
- 用户端预约卡内可选资源 chips（点选声明），T44 断言组（集成达 45 项）、契约 31 端点。

## [1.4.0] - 2026-09-14

第十三轮：课题更名「实验室资源管理与预约候补一体化系统」后，补齐标题承诺的资源管理能力。

### 新增
- 实验室资源（设备）清单：assets 表（schema 幂等建表，唯一索引保证实验室内资源名唯一）；管理端新增/修改（名称/规格/数量/状态 AVAILABLE·MAINTENANCE·DISABLED），用户端只读展示（不含已停用）；预约页实时显示所选实验室的资源清单（T41）；
- 资源利用率统计：按实验室聚合区间内开放场次/总席位/有效预约/已签到/已爽约，利用率=有效预约÷总席位；统计页新增利用率表与 CSV 导出，数据与 SQL 对账（T42）；
- 种子数据：三实验室预置五类典型资源（含维修中示例），演示开箱即用。

### 说明
- 集成断言组扩至 44 项（T41/T42），契约测试扩至 30 端点。

## [1.3.0] - 2026-09-13

第九至十二轮：管理员门户与业务规则深化（多智能体并发开发）。

### 新增
- 管理门户：登录页「用户/管理员」双入口（role_hint，403 ROLE_MISMATCH 不建会话不计失败）+ 独立管理控制台 `admin.html`（概览卡片、实验室管理、场次发布/修改、全员记录、用户管理、统计图表+CSV、运行指标、通知发布/历史、运行日志）；
- 业务规则：时段重叠检测（同用户预约/候补不得时间重叠，409 TIME_CONFLICT）；爽约信用（近 7 天爽约 ≥2 次限制新预约，409 PENALTY_ACTIVE，管理端可见计数）；历史归档（sweep 周期清理 30 天前候补与请求回执）；
- 运营能力：用户管理（搜索/停用立即下线/启用/重置密码一次性口令）；全员广播与定向通知（kind=NOTICE/REMIND）+ 发送历史；场次开始提醒（--remind-sec，reminded_at 防重）；自动备份轮转（--backup-interval，保留 7 份）；`--demo-days` 演示历史数据生成（固定种子可复现）；
- 安全纵深：CSP/nosniff/X-Frame-Options/Referrer-Policy 经 additional_header 覆盖全部响应（含静态页）；
- 用户体验：独立签到页（状态机 + 30s 自动刷新）、通知面板分页、取消预约二次确认、我的记录状态筛选、操作日志按动作/操作者筛选。

### 变更
- notifications.kind CHECK 扩展 NOTICE/REMIND（旧库自动重建迁移）；slots 新增 reminded_at 列（幂等迁移）；版本号 1.1.0 → 1.2.0（API 层）与 UI 文案同步。

### 测试
- 集成 T31–T40（role_hint/场次修改/通知/用户管理/提醒/备份/信用/重叠/归档/日志），--full 42 项全绿；契约 25 端点+14 错误场景；Playwright E2E 5 用例；ASan 通道转正式门禁。

## [1.2.0] - 2026-09-11

第六轮：深度优化（性能 / 可靠性 / 可观测性 / 安全 / 质量保障）。

### 新增
- 性能：每工作线程 SQLite 连接复用 + 预编译语句 LRU 缓存（32/连接）。基准对比：读路径吞吐 +40%~+600%（20 并发 451→3155 rps，p50 -86%），写路径 +10%~+100%；
- 可靠性：故障注入第三类 sweep-mid（爽约回收事务中途崩溃，exit 88）验证扫描原子性；语句看门狗（progress handler 按 5 秒墙钟打断慢查询 → 503）；浸泡实验 `tests/soak.py`（60 秒冒烟 34970 请求零错误、工作集 +3MB）；
- 可观测性：`src/log.c` 分级日志（INFO/WARN/ERROR + 5MB×3 轮转）、请求访问日志、慢请求告警（`--slow-ms`）；
- 安全：`X-Frame-Options`/`Content-Security-Policy`/`Referrer-Policy` 安全响应头；`--backup` 在线备份（SQLite Backup API）；
- 质量保障：`tests/fuzz.py` 模糊稳健性实验（240 次恶意输入零崩溃零 5xx）；gcov 覆盖率测量（全模块行覆盖 84.6%~98%）；CI 新增 MSYS2 ASan 内存安全通道。

### 修复
- T24 时序脆弱：限流探测改多轮脉冲，慢机下补充窗口横跨请求不再误报。

## [1.1.0] - 2026-09-11

第五轮：双智能体并发升级（容量制 / 限流防爆破 / 运行指标 / CI）。

### 新增
- 容量制（schema v3）：场次支持 1..200 席位，取消/爽约释放后 FIFO 连续补位；单占用唯一索引退役，容量约束由单写者事务校验；v2 库幂等迁移（含迁移实测）；
- 登录防爆破与写操作限流（`src/ratelimit.c`）：429 `LOGIN_LOCKED` / `RATE_LIMITED`，新增 `--rate-burst`、`--rate-refill-sec`、`--login-max-fails`、`--login-lockout`；
- 运行指标（`src/metrics.c`）：无锁计数器与毫秒级延迟直方图，`GET /api/admin/metrics` 与管理端指标面板；
- CI 持续集成（GitHub Actions：构建 + 单元测试 + 快速集成 + 静态分析）与性能基准脚本（`tests/benchmark.py`）；
- 一键演示服务脚本 `scripts/start-demo.ps1`。

### 修复
- `publish_slots` 变参宽度缺陷（int 形参经 `i` 绑定按 8 字节读取导致 CHECK 失败）；
- `RateBucket` 初始化哨兵与时间戳 0 碰撞（t=0 边界单测暴露）；
- T22/T24 时序脆弱（签到窗口参数化、限流探测多轮脉冲化）。

## [1.0.0] - 2026-09-10

首个完整交付版本。

### 新增
- 核心预约业务：场次查询、预约、取消、FIFO 候补与自动补位、请求去重回执、替代时段提示；
- 签到与爽约管理、站内通知、改密与在线会话管理、记录分页、统计与 CSV 导出（schema v2 幂等迁移）；
- 安全基线：libsodium 密码哈希、Cookie 会话 + CSRF/Origin 校验、过期会话清理；
- 可靠性验证：并发争抢实验（720 次请求零重复占用）、两类进程中断恢复实验（20 次）、单元与集成测试体系、`-fanalyzer` 静态分析与加固构建；
- 浏览器端到端验收、完整文档（契约/测试报告/进度/证据）。
