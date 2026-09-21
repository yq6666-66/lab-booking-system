# 分阶段优化计划

> 依据：[COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md)（2026-09-21 GitHub 同类项目对比）· 基线：v1.16.0（r42）
>
> **总原则**：① 不破坏"单二进制 + vendor 零网络依赖 + 回环部署"的定位；② 每项改动必须在既有 17 类测试通道上复验，性能基准（读 1910 rps）不得回退；③ 功能新增均需同步 CONTRACT.md 并通过 doc_test 一致性门禁；④ 论文/答辩材料同步更新。
>
> 状态标记：✅ 已完成 · 🚧 进行中 · ⬜ 未开始

---

## Phase 0 — 立即可落地的快赢（随本分支交付）

### 0.1 ✅ 补全 `Permissions-Policy` 安全响应头 + 修复 handler 安全头缺口

- **动机**：对比 D2——安全头矩阵仅缺 Permissions-Policy 一项；落地验证中进一步发现既有缺陷：CivetWeb `additional_header` 仅覆盖静态文件回复，**全部 `/api/*` 与 `/metrics` 响应实际从未携带 CSP/nosniff/XFO/Referrer-Policy**，与契约文档"对全部响应下发"的声明不符。
- **方案**：`src/http.c` 新增共享常量 `SEC_HEADERS`（五头含新增 `Permissions-Policy: camera=(), microphone=(), geolocation=()`），在 API JSON 单点发送路径与 `/metrics` 两处下发；静态页继续走 `additional_header`，两处内容一致；`docs/CONTRACT.md` 安全头条目改为如实描述双通道机制。
- **验收**：构建+25 单元+快速集成全绿；`curl -i` 在 /api/health、/metrics、静态页三处均可见五头；契约文档一致性通过。

---

## Phase 1 — P0 功能补强（目标 1–2 周/项，答辩前可选做）

### 1.1 ⬜ outbox 派发器 MVP（webhook + 审计日志通道，at-least-once）

- **动机**：对比 D5 最大缺口——`outbox` 表（channel/payload/sent_at/attempts/last_error）已预留却无派发器；全部一线竞品均有出站通知。
- **方案**：后台扫描线程（与 sweep/remind 线程同模式）周期取 `sent_at IS NULL AND attempts < N` 行；`channel='log'` 落结构化审计日志、`channel='webhook'` 经 CivetWeb client API（`mg_download`）POST 到实验室配置的回调 URL；指数退避（1/4/16 分钟）+ `attempts++` + `last_error` 记录；成功置 `sent_at`。**不引入 SMTP 依赖**（回环定位），SMTP 留待有真实邮件网关需求时评估。
- **验收**：① 契约新增管理端 webhook 配置端点与文档；② 集成测试注入 webhook 行→断言派发顺序与重试语义（宕机重启后续投，不重复成功行）；③ 故障注入通道覆盖"派发中途 kill"场景。
- **风险**：出站 HTTP 在回环部署下默认无目标——实现为 opt-in（`--outbox-webhook URL`），关闭时零行为变化。

### 1.2 ⬜ OpenAPI 3.1 契约 + doc_test 扩展

- **动机**：对比 D5/D6——手写 CONTRACT.md 无法被 Swagger UI/代码生成/自动化测试消费；libook 已示范自动文档路线。
- **方案**：新增 `docs/openapi.yaml`（先覆盖核心 20 端点：认证/预约/候补/签到/通知）；`doc_test.py` 增加第 8 项门禁：openapi.yaml 声明的 (method,path) 必须是 CONTRACT.md 的子集且在 http.c 中有路由，防止双文档漂移。
- **验收**：doc_test 8 项全绿；Swagger Editor 校验通过；README 文档表新增条目。
- **风险**：低——纯文档+测试扩展，不改运行时。

### 1.3 ⬜ 通知未读数与轻量实时化评估

- **动机**：对比 D5——竞品与实时预约系统普遍有实时反馈；本项目通知靠进页拉取。
- **方案**：第一步为 `GET /api/me/notifications` 响应附 `unread` 计数并在前端导航栏显示徽章（无新端点，改动最小）；第二步评估 **SSE**（`text/event-stream` 长连接 + 通知 INSERT 后信号量唤醒）对比 30s 轮询的收益，CivetWeb 需验证 chunked 响应与工作线程占用（8 线程 × 长连接上限需压测），不满足则止步于徽章+可配置轮询。
- **验收**：徽章全绿不回归；若做 SSE：浸泡测试 300s 无连接泄漏，并发 50 长连接下读写基准不回退 >10%。
- **风险**：SSE 占用 CivetWeb 工作线程——先压测后决策，设硬性回退点。

---

## Phase 2 — P1 体验与安全增强（3–4 周/项，论文加分项）

### 2.1 ⬜ QR 码签到

- **动机**：签到目前需登录网页操作；QR 到场即扫即签是实验室场景自然交互；领域竞品均未实现，属可写进论文的体验创新点。
- **方案**：引入 [nayuki/QR-Code-generator](https://github.com/nayuki/QR-Code-generator)（6.8k★，单文件 C 实现 qrcodegen.c/h，MIT）入 vendor 并锁 SHA256；签到页生成一次性签到令牌 → QR（SVG 输出，零前端依赖）；管理员端扫码/输入令牌代签或用户自查。令牌短时效（≤5 分钟）且单次有效，与现有 request_receipts 幂等语义对齐。
- **验收**：单元测试覆盖令牌生成/过期/重放拒绝；E2E 新用例走"生成→展示→核销"闭环；vendor_verify.py 纳入新依赖校验。

### 2.2 ⬜ iCal token 化订阅 URL

- **动机**：对比 D5——已有 `/api/me/calendar.ics` 但需登录态；竞品（Easy!Appointments/Thunderbird Appointment）均支持日历客户端订阅。差"免登录只读订阅"一步。
- **方案**：用户生成/吊销个人订阅令牌（复用 api_tokens 表模式），`GET /api/ical/{token}` 免登录返回该用户未来 N 天 ICS；令牌可吊销、只读、作用域最小化。
- **验收**：契约+集成测试覆盖令牌生命周期；吊销后 401；文档标注"令牌即凭据"安全提示。

### 2.3 ⬜ 管理端 TOTP 二因素认证

- **动机**：对比 D2——管理端高权限操作（强制取消/代签退/审批）仅口令单因素；glewlwyd（433★）与 OTPClient（557★）证明纯 C 栈可行。
- **方案**：RFC 6238 TOTP（SHA-1/30s/6 位，兼容主流验证器 App）；HMAC 用 libsodium `crypto_auth` 族，Base32 解码自实现（<100 行）；仅对 ADMIN 角色强制启用，登录第二步校验；提供恢复码防锁定。
- **验收**：单元测试 RFC 6238 附录 B 测试向量；集成测试覆盖绑定→登录→恢复码→解绑全生命周期；时序侧信道（非常时比较）专项断言。
- **风险**：中——改动登录流，需保证"未启用用户零感知"与全部既有认证测试不回退。

### 2.4 ⬜ 统计报表增强

- **动机**：对比 D3/A——LibreBooking 报告体系成熟；本项目已有 SVG 柱状图与 CSV 导出，缺时间趋势维度。
- **方案**：利用率/爽约/公平指数按周聚合快照表（后台 sweep 顺带写入），管理台折线图（沿用零依赖 SVG 路线），CSV 导出扩展趋势数据。
- **验收**：契约同步；聚合 SQL 有基准测试；前端无障碍标签延续。

---

## Phase 3 — P2 架构演进（视毕业后续需求排期）

### 3.1 ⬜ WebSocket 实时通道（席位变动广播）

- **动机**：SSE 的能力上限（单向）之上，竞品（kore/facil.io 内建 WS、realtime booking 项目）示范了实时席位视图价值。
- **方案**：CivetWeb 原生 WebSocket 支持；广播维度收敛为"单场次席位变动"事件（并发争抢场景的核心观测点）；认证复用会话 cookie + Origin 校验；连接数上限与令牌桶限频防资源耗尽。
- **验收**：720 并发争抢实验扩展 WS 观测端；断线重连+幂等序号；浸泡无泄漏。

### 3.2 ⬜ OIDC/LDAP 学校统一认证对接

- **动机**：对比 D2/A——LibreBooking 支持 LDAP/AD；高校真实部署需对接学校统一身份。
- **方案**：评估 [liboauth2](https://github.com/OpenIDC/liboauth2)（140★，C）或最小化自实现 OIDC Authorization Code Flow（依赖 CivetWeb client + cJSON + libsodium，均在栈内）；本地口令登录保留为回退。
- **风险**：外部 IdP 联调依赖环境，建议毕业后再投入。

### 3.3 ⬜ 容器化部署

- **动机**：对比 D6——竞品普遍官方 Docker；Linux 服务器部署形态缺口。
- **方案**：多阶段 Dockerfile（MinGW→Linux 工具链交叉评估，或原生 gcc+musl 构建）；compose 编排含备份卷；CI 增加构建 job。与 Windows 三件套路线并存。
- **验收**：install_test 等价物在容器内通过；镜像含 healthcheck。

### 3.4 ⬜ i18n 英文界面

- **动机**：对比 D6——成果国际可见性；Easy!Appointments 多语言成熟度示范。
- **方案**：前端抽取字符串表（ui.js 基座统一渲染），`?lang=` + localStorage 记忆；后端错误码已是英文枚举，仅翻前端。
- **验收**：frontend_test 扩展双语快照断言。

### 3.5 ⬜ 性能持续调优

- **动机**：守护 D1/D3 优势——读 1910 rps 基线不回退，写路径随功能增长需持续观测。
- **方案**：EXPLAIN QUERY PLAN 门禁进 doc_test（新 SQL 必须走索引）；slots 列表页覆盖索引评估；metrics 直方图桶与 SLO（p99 < 50ms）对齐；bitmap_bench 结论回看是否产品化。
- **验收**：benchmark.py 基线对比报告进 docs/evidence/。

---

## 排期总览与依赖关系

```
Phase 0（本分支）───────┐
                        ├─→ Phase 1.2 OpenAPI（独立，随时可做）
Phase 1.1 outbox ───────┤
                        ├─→ Phase 1.3 通知实时化 ─→ Phase 3.1 WebSocket
Phase 2.3 TOTP ─────────┤（认证流改动，宜在 2.1/2.2 之前稳定）
Phase 2.1 QR 签到 ──────┤
Phase 2.2 iCal 订阅 ────┴─→ Phase 3.3 容器化 ─→ Phase 3.2 OIDC
```

**排序依据**：价值/成本比（outbox 表已预留、OpenAPI 零运行时风险）→ 答辩加分密度（QR 签到、TOTP 可写进论文创新点）→ 架构演进（依赖前序稳定）。

**守护性约束（贯穿全部阶段）**：任何一项合并前须通过——构建+25 单元、快速集成、契约+文档一致性、`--full` 全量（并发+故障注入）、性能基准不回退；ASan 通道在改 C 源码的项上必跑。
