# GitHub 同类项目对比分析报告

> 检索日期：2026-09-21 · 基线版本：本项目 v1.16.0（r42） · 分析者口径：以"实验室/教室/设备/座位预约"领域同类系统为主体，辅以通用排期平台与 C 语言 Web 技术栈两翼参照。
>
> 配套文档：优化建议与分阶段落地计划见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)。

---

## 1. 检索方法

使用 GitHub Search API（`gh api search/repositories`）执行 4 轮共 22 组查询，查询词覆盖：

- 领域词：`laboratory booking system`、`lab management`、`seat reservation`、`library seat booking`、`equipment booking`、`room booking system`、`classroom booking`
- 通用词：`appointment scheduling`、`booking system scheduler open source`、`self-hosted scheduling`、`booking websocket realtime`、`waitlist queue fairness`
- 技术词：`civetweb`、`kore io`、`facil.io`、`sqlite web application c`、`server-sent-events c`、`totp c`、`qrcode generator c`、`openid connect c library`、`reservation system c language`

筛选标准：领域同类取"功能完整度与活跃度"最优者（不唯星数），技术参照取 C 语言 Web 领域头部项目。星数为 2026-09-21 快照。本报告为快照式分析，建议每学期复跑一轮（查询词已在上方列全，可直接复现）。

---

## 2. 对标项目全景

### 梯队 A：领域同类（实验室/教室/设备/座位预约）

| 项目 | 星数 | 技术栈 | 定位与要点 |
|---|---|---|---|
| [LibreBooking/librebooking](https://github.com/LibreBooking/librebooking) | 809 | PHP 8.2 / MySQL | 资源调度领域标杆（Booked Scheduler 后继）：资源预约、配额、审批、黑名单时段、公告、邮件提醒、报告、iCal、LDAP/AD、reCAPTCHA、官方 Docker 镜像与文档站，社区活跃（Discord） |
| [alextselegidis/easyappointments](https://github.com/alextselegidis/easyappointments) | 4388 | PHP / CodeIgniter / MySQL | 自托管预约排期平台：服务-提供者模型、**Google Calendar 双向同步**、REST API、邮件通知、工作计划与预约规则、多语言、GDPR 同意管理 |
| [julia-/room-booking-system](https://github.com/julia-/room-booking-system) | 578 | Node.js / ReactJS | 通用房间预约系统，前后端分离典型实现 |
| [thunderbird/appointment](https://github.com/thunderbird/appointment) | 552 | Python | Thunderbird 家族排期工具：日历连接、时段分享、邮件邀约 |
| [classroombookings/classroombookings](https://github.com/classroombookings/classroombookings) | 223 | PHP / CodeIgniter 3 | 学校教室预约：学年课表/学期日历、部门与角色权限、商用托管+自托管双轨 |
| [neokoenig/RoomBooking](https://github.com/neokoenig/RoomBooking) | 192 | Web 日历式 | 日历视图驱动的房间预约 |
| [mikaeljorhult/hydrofon](https://github.com/mikaeljorhult/hydrofon) | 29 | PHP / Laravel 11 | 设备预约：Bucket（可互换资源集合）、Group 可见性控制、CTE 查询 |
| [yeliudev/SeatKiller](https://github.com/yeliudev/SeatKiller) | 26 | Python | 武汉大学图书馆抢座脚本——证明"API 化预约接口 + 自动化"存在真实需求 |
| [sjtu-libook/libook](https://github.com/sjtu-libook/libook) | 16 | Django + React | 图书馆座位预约全栈系统：**Swagger 自动生成 API 文档**、Coveralls 覆盖率、前后端双 CI |

其余检索命中多为课程设计级作品（PHP/Java 学生项目，星数 ≤15，无候补/公平/并发语义），不具对标价值，仅证明该领域需求广泛。

### 梯队 B：通用排期平台（功能设计参照）

| 项目 | 星数 | 技术栈 | 参照价值 |
|---|---|---|---|
| cal.com（calcom/cal.diy） | 48,572 | TypeScript | 开源 Calendly 替代的体验标杆：日程规则引擎、工作流自动化、视频会议集成 |
| [lukevella/rallly](https://github.com/lukevella/rallly) | 5,262 | TypeScript | 投票式排期、自托管 Docker 化 |
| [Tymeslot/tymeslot](https://github.com/Tymeslot/tymeslot) | 205 | Elixir / Phoenix LiveView | 实时交互式排期（LiveView 实时性参照） |

### 梯队 C：C 语言 Web 技术栈（架构参照）

| 项目 | 星数 | 参照价值 |
|---|---|---|
| [jorisvink/kore](https://github.com/jorisvink/kore) | 3,825 | C Web 平台"secure by default"典范：**特权分离（privsep）+ seccomp/pledge 沙箱**、默认 TLS、WebSocket、ACME 自动证书、声明式参数校验、异步 PostgreSQL、后台任务 |
| [boazsegev/facil.io](https://github.com/boazsegev/facil.io) | 2,403 | 事件驱动 C 框架：HTTP/WebSocket、pub-sub 模式 |
| [Corvusoft/restbed](https://github.com/Corvusoft/restbed) | 2,000 | C++ 异步 REST 框架 |
| [babelouest/glewlwyd](https://github.com/babelouest/glewlwyd) | 433 | **纯 C 实现的 SSO/OAuth2/OIDC 认证服务器，含 MFA（TOTP）**——证明 C 栈可承载 2FA |
| [paolostivanin/OTPClient](https://github.com/paolostivanin/OTPClient) | 557 | C 语言 TOTP/HOTP 参考实现 |
| [nayuki/QR-Code-generator](https://github.com/nayuki/QR-Code-generator) | 6,777 | 高质量二维码生成库，**提供单文件 C 实现（qrcodegen.c/h）** |
| [civetweb/civetweb](https://github.com/civetweb/civetweb) | 3,454 | 本项目已 vendor 的 HTTP 服务底座（内置 WebSocket 支持） |

---

## 3. 功能矩阵对比

| 能力 | 本项目 v1.16.0 | LibreBooking | Easy!Appointments | classroombookings | libook |
|---|---|---|---|---|---|
| 容量制预约 + 实时余量 | ✅ | ✅ | ✅（1 对 1 模型） | ✅ | ✅ |
| 候补队列 | ✅ **三档策略** | ✅（基础 waitlist） | ❌ | ❌ | ❌ |
| 候补老化加权/防饿死 | ✅ score=1000p+200(c−5)+60log₂h | ❌ | ❌ | ❌ | ❌ |
| 审批工作流 | ✅ 批量+部分成功语义 | ✅ | ✅ | ✅ | ❌ |
| 信用/爽约治理 | ✅ 账户+周回补+限约 | ❌（仅配额） | ❌ | ❌ | ❌ |
| 公平性度量 | ✅ Jain 指数审计 | ❌ | ❌ | ❌ | ❌ |
| 优先级抢占+补偿 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 资源配额声明/维护工单/资格授权 | ✅ | 部分（资源属性） | ❌ | ❌ | 部分 |
| 邮件/外部通知 | ❌（outbox 表已预留未实现） | ✅ | ✅ | ✅ | ✅ |
| 日历集成 | ✅ iCal 导出（本机） | ✅ iCal 订阅 | ✅ **Google 双向同步** | ❌ | ❌ |
| QR 码签到 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 2FA/MFA | ❌ | ❌（reCAPTCHA） | ❌ | ❌ | ❌ |
| Prometheus 指标 | ✅ 文本格式+JSON 双端点 | ❌ | ❌ | ❌ | ❌ |
| OpenAPI/Swagger 文档 | ❌（手写 CONTRACT.md） | ❌（文档站） | ✅（REST API 文档） | ❌ | ✅ **自动生成** |
| Docker 部署 | ❌（三件套绿色迁移） | ✅ 官方镜像 | ✅ | ✅ | ❌ |
| i18n 多语言 | ❌ | ✅ | ✅ 多语言 | ❌ | ❌ |
| WebSocket/SSE 实时推送 | ❌（按需拉取） | ❌ | ❌ | ❌ | ❌ |

> 注：竞品能力基于其 README/官方文档描述；"✅"仅表示具备该能力域，实现深度不一。

---

## 4. 六维深度对比

### D1 并发一致性与候补公平性——本项目显著领先

同类竞品的并发正确性基本依赖关系数据库行锁（PHP/MySQL 生态典型做法），普遍缺乏对"超卖"与"候补饥饿"的显式治理。本项目以 SQLite `BEGIN IMMEDIATE` 单写者事务 + 持久化回执幂等 + 720 次并发争抢零超卖的实验证据，以及三档候补策略（含老化加权消除同档饿死）+ Jain 公平指数量化审计，构成了检索范围内**独一无二的语义深度**。这一维度无需补课，是答辩与论文的差异化核心；计划中的工作仅是"守护"性质（性能基准不回退、属性测试覆盖）。

### D2 安全——基础扎实，缺 2FA 与进程加固

本项目已具备：Argon2id 口令哈希（含用户不存在时的 dummy 验证防时序侧信道）、会话 SHA-256 落库、CSRF 常时比较、Origin/Host 校验、登录防爆破+每用户令牌桶限流、CSP/nosniff/XFO/Referrer-Policy/Permissions-Policy 安全头全响应覆盖（v1.16.0 后 API 与 /metrics 响应经共享常量补齐）、16KB 请求体上限、JSON 重复键检测。对比之下：

- **kore** 的"secure by default"走得更远：特权分离 + seccomp/pledge 系统调用沙箱 + 默认 TLS + ACME。本项目纯回环部署下 TLS/HSTS 缺位是合理取舍，但**缺少 TOTP 二因素认证**（glewlwyd/OTPClient 证明纯 C 可行），管理端高权限操作仅有口令单因素保护。
- LibreBooking 用 reCAPTCHA 对抗自动化滥用，本项目以登录锁定+限流替代，回环场景足够，公网部署需重估。

### D3 可观测性——差异化强项，同类几乎空白

检索范围内仅本项目提供 Prometheus 文本格式 `/metrics` + 管理端 JSON 快照双通道、延迟直方图、分级轮转日志、慢查询打断（5 秒 progress handler）与在线备份。LibreBooking/Easy!Appointments/classroombookings 均无内建指标端点。该维度建议保持并小幅增强（直方图桶与 SLO 对齐），无需对标补齐。

### D4 测试工程——大幅领先

本项目 17 类测试通道（集成 79 断言、720 并发争抢、30 次故障注入、契约 52 端点、E2E、单元、浸泡 51,140 请求、模糊 240 恶意输入、属性测试、差分对拍、安装演练、四通道 CI 含 ASan）远超全部对标项目——多数竞品仅有单元测试+基础 CI，libook 的 Coveralls 覆盖率接入是竞品中最好的实践。测试工程是本项目的护城河，后续新增功能必须沿用"同通道复验"纪律。

### D5 通知与外部集成——最大功能缺口

这是对比中最清晰的差距：**全部一线竞品都有出站通知**（LibreBooking 邮件提醒、Easy!Appointments 邮件+Google 同步、classroombookings 通知、libook 站内+邮件），而本项目 notifications 站内通知完整、`outbox` 出站队列表已预留（channel/payload/sent_at/attempts/last_error，注释"至少一次语义"），**但无任何派发器代码**。日历方面本项目有 iCal 导出双端点但需登录态，缺少竞品普遍支持的"token 化订阅 URL"（日历客户端自动拉取）。此外 libook 的 Swagger 自动文档对比本项目手写契约，在"契约即代码"上落后一个身位。

### D6 部署与国际化——定位使然的取舍

竞品普遍提供 Docker（LibreBooking 官方镜像、Easy!Appointments、classroombookings、rallly），本项目走"exe+DLL+web 三件套绿色迁移+空目录安装演练"路线，对高校机房 Windows 环境是合理选择，但容器化是公网/机房 Linux 部署的缺口。i18n 方面 Easy!Appointments 多语言成熟，本项目纯中文界面，制约成果的国际可见性。

---

## 5. 结论

**保持性优势（不回退、持续守护）**：单写者事务与零超卖证据、三档候补+老化加权、公平性审计、Prometheus 观测、17 类测试通道、无障碍前端。

**差距清单（按价值/成本排序，展开为分阶段计划）**：

1. **outbox 派发器缺失**——表结构已预留，只差派发器；对标全部竞品的出站通知能力，是"从 90% 到 100%"的最高性价比项。
2. **无 OpenAPI 机器可读契约**——手写 CONTRACT.md 无法被工具链消费；libook 已示范自动生成路线。
3. **无 2FA**——管理端单因素口令；纯 C 参考实现充分（TOTP）。
4. **无 QR 码签到**——移动端签到体验缺口；nayuki/QR-Code-generator 提供单文件 C 实现。
5. **无 iCal token 化订阅**——已有 ics 导出，差"免登录订阅 URL"一步。
6. **无 WebSocket/SSE 实时推送**——通知靠进页拉取；civetweb 底座已支持 WebSocket，SSE 更轻。
7. **无容器化/i18n**——部署与受众扩展项，非功能正确性问题。

上述差距的逐项方案、验收标准与排期见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)。
