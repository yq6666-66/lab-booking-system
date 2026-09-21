# Phase 1 实施蓝图：outbox 派发器 · OpenAPI 契约 · 通知实时化

> 纯规划文档，无代码改动。设计对齐主项目 v1.16.0+（schema v5，`outbox` 表已存在）。

---

## 1.1 outbox 派发器（webhook 通道，at-least-once）

### 目标与前置

补齐竞品普遍具备的出站通知能力。`outbox` 表与 `outbox_pending` 索引已存在（db.c:291-292），全库无派发器代码——本项只差"写侧入队 + 后台派发 + 管理面"三段。

真实 schema（已在工作树核对）：

```sql
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id INTEGER REFERENCES notifications(id),
  channel TEXT NOT NULL, payload TEXT NOT NULL,
  sent_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(sent_at,id);
```

### 数据模型

- **不加列方案（推荐 v1）**：退避判定由既有列推导——派发条件 `sent_at IS NULL AND attempts < :max AND :now - created_at >= backoff(attempts)`，`backoff(n)=60*4^(n-1)` 秒（1/4/16/64 分钟，封顶 1 小时），无需 schema 迁移。
- 备选：加 `next_attempt_at` 列（schema v6 幂等迁移），派发查询更直白但多一次迁移。仅当实测过滤成本显著时启用。
- **死信判定**：`sent_at IS NULL AND attempts >= max_attempts`（默认 8）。不新增状态列，管理端死信查询即此 WHERE。

### 写侧入队（与业务同事务）

- `notify()`（service.c 通知单点）内追加：若 `cfg->outbox_webhook` 非空，同事务 INSERT `outbox(notification_id, channel='webhook', payload, created_at)`。
- payload 为自包含 JSON 信封，接收方按 `id` 幂等去重：

```json
{"id":42,"event":"PROMOTED","occurred_at":1789000000,
 "data":{"username":"user03","lab":"实验室A","slot_id":118,"start_at":"...","title":"..."}}
```

- **脱敏红线**：payload 只含业务展示字段，禁止入队口令哈希、会话令牌、email 等敏感列（验收断言项）。

### 派发线程与事务模型

- 与 sweep/remind 线程同模式（`mg_start_thread` + 周期 Sleep）：每 `--outbox-interval`（默认 60s，0 关闭）唤醒一次。
- 单轮流程：`BEGIN IMMEDIATE` 取一批（`sent_at IS NULL AND attempts<max AND backoff 满足 ORDER BY id LIMIT 20`）→ 逐行 `mg_download` POST（CivetWeb client API）→ 成功 `UPDATE sent_at=now`；失败 `attempts=attempts+1, last_error=截断256字节` → `COMMIT`（每行独立短事务，避免长写锁）。
- **at-least-once 语义**：POST 成功后 UPDATE 前崩溃 → 重启重投，接收方按信封 `id` 去重；绝不丢、可能重，与 outbox 表注释"至少一次"一致。
- 审计：首轮派发成功/进入死信各记 `operation_events(action='OUTBOX_SENT'/'OUTBOX_DEAD')`。

### API 面（契约同步 + doc_test 联动）

| 端点 | 说明 |
|---|---|
| `GET /api/admin/outbox?state=pending\|sent\|dead&page=&page_size=` | 分页，dead 含 last_error |
| `POST /api/admin/outbox/{id}/retry` | 重置 attempts=0、清 last_error（仅 dead 可重试） |
| `PUT /api/admin/webhook` `{url}` | 空串=停用；写 settings kv（见下） |

- **配置存储**：全局配置目前全在 CLI。推荐新增 `settings(key TEXT PRIMARY KEY, value TEXT)`（schema v6 迁移）存 `webhook_url`，管理台可改免重启；CLI `--outbox-webhook` 作为首次默认值写入。备选：仅 CLI、重启生效（实现最薄，管理体验差）。

### CLI 开关

`--outbox-interval SEC`（默认 60，0 关闭）· `--outbox-webhook URL`（opt-in，默认空=不启用）· `--outbox-max-attempts N`（默认 8）· `--outbox-timeout MS`（默认 5000）。

### 安全考量

- webhook 是本回环系统**唯一出站网络行为**：URL 校验仅允许 http/https（拒 file/ftp）；超时硬限；失败不计入用户限流桶。
- SSRF 评估：URL 仅 ADMIN 可配（管理端鉴权 + 审计 WEBHOOK_SET），单实例部署下面向可信网络，风险可接受；文档明示"webhook 地址具备内网可达性即等于授权其接收全部通知数据"。
- 不实现 SMTP：回环定位无邮件网关依赖；webhook→邮件网关由接收侧转接。

### 测试矩阵映射

| 通道 | 用例 |
|---|---|
| 单元 | 退避公式边界、信封构造脱敏、死信判定 |
| 集成 | T-Outbox-1 注入通知→webhook 收到（测试内置接收器）· -2 失败重试退避时序 · -3 重启续投不丢 · -4 死信+retry 复活 · -5 开关关闭零行为 · -6 payload 无敏感字段 |
| 故障注入 | 派发中途 kill → 重启后 sent_at 行不重投、未 sent_at 行重投 |
| 契约 | 3 新端点形状 + 403 非 ADMIN |
| 文档 | CONTRACT 端点/错误码 + doc_test 7 项保持绿 |

### 验收清单 / 回滚 / 工作量

- 验收：上表全绿；基准（读 1910 rps）与既有通道零回退；演示实例起停无派发副作用（webhook 未配置时 outbox 恒空）。
- 回滚：`--outbox-interval 0` 即整体关闭；表已存在，无迁移回滚风险。
- 工作量：约 2–3 个迭代轮（派发线程+入队 1 轮；管理端点+契约+测试 1 轮；故障注入+复验 1 轮）。

### 参考

LibreBooking `Jobs/sendwaitlist.php` + `Jobs/JobCop.php`（候补通知作业与作业框架，已验证路径）为本项最直接对标；见 [references.md](references.md)。

---

## 1.2 OpenAPI 3.0 契约与 doc_test 扩展

### 目标与前置

手写 CONTRACT.md 无法被 Swagger UI/代码生成/自动化工具消费。libook（Django+React）已示范自动文档路线；本项目以"**受限格式手写 spec + 零依赖校验**"折中，保持零依赖原则。

### 规范与文件组织

- 选 **OpenAPI 3.0.3**（生态兼容最广；3.1 的 JSON Schema 方言对本项目无增益）。
- `docs/openapi.yaml` 单文件，分节顺序与 CONTRACT.md 一致（认证→预约生命周期→候补→通知→凭据→管理→观测）。
- **v1 覆盖 20 核心端点**：login、register、me、health、labs、slots、reservations(POST/cancel/checkin/checkout/reschedule/records)、waitlist(join/withdraw/mine)、notifications(+read)、calendar.ics、tokens(create/revoke/list)。
- 复用组件：`ErrorEnvelope`（code/message/data）、`PageEnvelope`（total/page/page_size/has_more）、`Reservation`、`Lab`、`Slot`、`WaitlistEntry`；错误码枚举表与 CONTRACT 对齐（doc_test 已有错误码一致性正则可复用）。

### 零依赖校验设计（doc_test 第 8 项）

- doc_test 现零第三方依赖——不引 PyYAML。新增专用解析：只识别 `paths:` 段下缩进固定的 `  /api/...:` 与 `    get:`/`    post:` 两级结构，行级正则提取 `(method,path)` 集合。
- 校验规则：**openapi 集合 ⊆ CONTRACT 集合 ⊆ http.c 路由字面量**（双向防漂移：openapi 多写报错，contract 新增未入 spec 仅告警提示补齐）。
- 文件头注释声明"仅支持受限书写格式（paths 2 空格缩进、方法 4 空格缩进），规范合法性用外部编辑器校验"；README 给 [Swagger Editor](https://editor.swagger.io/) 校验说明（本地可选步骤，不进 CI）。

### 验收清单 / 回滚 / 工作量

- 验收：doc_test 8 项全绿；20 端点在 Swagger Editor 校验通过（人工一次性）；CONTRACT/openapi/http.c 三方一致性成立。
- 回滚：纯文档+测试扩展，删除文件即回滚。
- 工作量：1–2 轮（spec 编写 1 轮；doc_test 扩展+对拍 1 轮）。

### 参考

easyappointments `application/controllers/api/v1/Appointments_api_v1.php`（REST 版本化控制器命名，已验证）；libook Swagger UI（自动生成路线对照）。

---

## 1.3 通知实时化：unread 徽章 → SSE 评估

### 第一步：unread 计数（低风险，先行）

- `GET /api/me/notifications` 响应顶层加 `unread`（`SELECT count(*) WHERE user_id=? AND read_at IS NULL`，与列表查询同事务快照）。
- 前端 ui.js 通知面板加未读徽章；无需新端点、无迁移。
- 验收：既有通知断言不回退 + 新增 unread 断言；前端无障碍标签延续。

### 第二步：SSE（Server-Sent Events）可行性评估——先测后做

- **约束测算先行**：CivetWeb 8 工作线程，每 SSE 长连接独占一线程至断开 → 理论上限 8 并发流。回环部署用户规模 ≤几十人场景：为 SSE 设连接上限（如 4），超出返回 `503 SSE_BUSY` 前端回退 30s 轮询（`--notify-poll-sec` 可配）。
- 实现要点：`/api/me/stream` handler 输出 `Content-Type: text/event-stream`；15s 注释行心跳防超时（request_timeout_ms=5000 需对该路径豁免或以心跳续期）；事件源为 `notify()` 写库后 `SetEvent` 广播信号量；v1 忽略 Last-Event-ID（EventSource 自动重连后客户端全量重拉 notifications 补齐，语义无损）。
- **硬性回退点**：50 并发长连接压测下 benchmark 读基线回退 >10% 或出现线程饥饿 → 放弃 SSE，止步徽章+轮询。
- 验收（若做）：浸泡 300s 无连接泄漏（句柄计数）；断线重连语义断言。

### 参考

LibreBooking `Jobs/sendreminders.php`（轮询式作业通知——若 SSE 评估失败，其"定时作业+站内通知"即是回退形态的竞品印证）。
