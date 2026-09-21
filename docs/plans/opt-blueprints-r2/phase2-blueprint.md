# Phase 2 实施蓝图：QR 码签到 · iCal 订阅 · TOTP 二因素

> 纯规划文档，无代码改动。三项相互独立，但 TOTP 的二维码复用 QR 能力（见 2.3 依赖说明）。

---

## 2.1 QR 码签到

### 目标与前置

到场"扫码即签"的自然交互；领域竞品（LibreBooking/Easy!Appointments/classroombookings/libook）均未实现，可作为论文体验创新点。前置：vendor 引入 nayuki/QR-Code-generator 的 C 实现。

### 依赖引入

- 引入 `c/qrcodegen.c`、`c/qrcodegen.h`（MIT，单文件，无外部依赖）入 vendor，同步 `docs/dependencies.lock.json` SHA256 与 `tests/vendor_verify.py` 第 6 项。
- 参考用法直接取上游 `c/qrcodegen-demo.c`（已验证存在）。

### 数据模型

```sql
CREATE TABLE checkin_tokens(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id INTEGER NOT NULL REFERENCES reservations(id),
  token_hash TEXT NOT NULL UNIQUE,   -- SHA-256，复用 hash_text 惯例
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  created_at INTEGER NOT NULL);      -- schema v6，与 settings 迁移同轮
```

- 一次性语义：核销即置 `used_at`，重放 409；过期 410。
- 令牌明文 128-bit 随机（`randombytes_buf` + hex），仅出现在生成响应与二维码内容里，落库只有哈希。

### API 面

| 端点 | 说明 |
|---|---|
| `POST /api/reservations/{id}/checkin-token` | 本人；仅 CONFIRMED 且处于签到窗口；返回 `{token, expires_in, svg}` |
| `GET /api/checkin/{token}` | **免登录**（扫码即签）：核销并复用 `booking(checkin)` 既有窗口/状态语义；返回迷你 HTML 结果页 |
| `POST /api/checkin/{token}` | 程序化核销（JSON），供集成测试与管理员代输兜底 |

- 签到页生成按钮进 `web/app.js`，二维码以**内联 SVG** 展示。
- **CSP 合规红线**（frontend_test 会抓）：qrcodegen SVG 输出必须无 `style="` 属性、无内联事件属性——`fill`/`shape-rendering` 用属性而非 style；验收加正则断言。

### 安全考量

- 免登录端点三重防护：token 128-bit 熵 + 哈希落库 + 全局令牌桶限速（复用 rl_register_gate 模式）；日志不落明文 token。
- 时间窗校验完全复用现有 checkin 窗口逻辑（--checkin-window），不另立规则。

### 测试矩阵映射

单元（令牌生命周期/TTL/一次性核销/QR 缓冲区尺寸）· 集成 T-QR-1..4（生成→免登录核销→重放 409→过期 410）· E2E 新 case（生成→展示→核销→记录页状态可见）· fuzz（/api/checkin/ 随机 token 零 5xx）· 契约（3 端点形状）· 前端门禁（SVG CSP 合规断言）。

### 回滚 / 工作量

vendor 纯增量；表与端点均为新增，关停=前端隐藏入口。约 2 轮（vendor+生成端 1 轮；核销端+E2E 1 轮）。

---

## 2.2 iCal 订阅 URL（token 化）

### 目标与前置

已有登录态 iCal 双端点（`/api/me/calendar/export`、`/api/me/calendar.ics`）；补"免登录只读订阅"，日历客户端可自动拉取——对标 Easy!Appointments/Thunderbird Appointment 的日历连接能力。

### 数据模型

- `users` 幂等加列 `ical_token TEXT`（NULL=未启用）。单用户单订阅，简化安全心智。
- 备选（不推荐 v1）：api_tokens 加 scope 列支持多订阅——留待多设备差异化需求出现。

### API 面

| 端点 | 说明 |
|---|---|
| `POST /api/me/ical-token` | 生成/旋转（旋转即吊销旧值），返回明文一次性展示 |
| `DELETE /api/me/ical-token` | 吊销 |
| `GET /api/ical/{token}` | **免登录只读**：未来 `--ical-days`（默认 30）天 CONFIRMED/HELD/PENDING 预约 ICS；`ETag`+`304` 支持客户端条件拉取 |

### 安全考量

- 文档明示"订阅 URL 即凭据"（含全部日程的只读视图）；审计记 `ICAL_SUBSCRIBE`（只记命中与否，不记 token）。
- 旋转/吊销立即生效；匿名限速复用全局注册桶。
- 响应头 `Cache-Control: no-cache`（内容可缓存但每次校验 ETag）。

### 验收 / 工作量

集成 T-ICAL-1..4（生成→订阅 200→旋转后旧 token 401→吊销后 401）+ ETag 304 断言 + 契约。约 1 轮（迁移+三端点+测试）。

---

## 2.3 管理端 TOTP 二因素认证

### 目标与前置

强制取消/代签退/审批等高权限操作目前仅口令单因素。纯 C 可行性已被 glewlwyd（SSO 服务器，`src/scheme/otp.c`）与 OTPClient（`src/common/`）验证。**仅 ADMIN 角色启用**，普通用户零感知。

### 关键技术决策：SHA-1 生态兼容问题

- RFC 6238 标准默认 HMAC-SHA-1，主流验证器 App（Google Authenticator 等）默认即 SHA-1；**libsodium 刻意不提供 SHA-1**，`crypto_auth` 是 HMAC-SHA-512-256，不能用于标准 TOTP。
- **方案 A（推荐）**：内嵌 RFC 3174 参考域 SHA-1 实现（公有域，约 200 行）+ 自实现 `hmac_sha1`（约 40 行）+ Base32 编解码（约 80 行）。兼容性最好，零外部依赖。
- 方案 B：HMAC-SHA-256 TOTP（RFC 6238 原生支持）零新代码依赖（libsodium `crypto_auth_hmacsha256`），但 Google Authenticator 需手动改算法，生态兼容差——列为 A 的可选附加项而非默认。
- 参数：8 位、30s 步长、±1 窗口漂移容忍（防时钟偏差误锁管理员）。

### 数据模型

`users` 幂等加列：`totp_secret TEXT`（Base32，NULL=未绑定）、`totp_enabled INTEGER DEFAULT 0`、`totp_recovery TEXT`（8 组恢复码的 SHA-256 哈希 JSON 数组，一次性使用）。

### 登录流改造（核心风险点）

1. `POST /api/login` 密码通过且 `totp_enabled=1` → **不建会话**，返回 `202 {pending_2fa:true, mfa_ticket}`（票据 120s、单次、内存态 + 落库 `mfa_tickets` 或复用会话半开状态——推荐独立短生命周期表，故障注入可测）。
2. `POST /api/login/2fa` `{mfa_ticket, code}` → 校验（`sodium_memcmp` 常时比较）→ 建正式会话 + CSRF。
3. 恢复码路径：code 命中恢复码哈希数组 → 消费该组并登录；剩余为 0 时响应提示重新生成。
4. **未启用用户路径零改动**——全部既有登录/注册/锁定测试必须原样通过（验收硬断言）。

### 管理面 API

| 端点 | 说明 |
|---|---|
| `POST /api/me/totp/setup` | 生成密钥 + `otpauth://totp/...` URI + **二维码 SVG（复用 2.1 能力——依赖说明：TOTP 排在 QR 之后实施可白得扫码绑定体验）** |
| `POST /api/me/totp/verify` `{code}` | 首次验证并激活 |
| `DELETE /api/me/totp` `{code}` | 需当前有效 TOTP 码才能解绑 |
| `GET /api/me/totp` | 状态与剩余恢复码数（不回明文/密钥） |

### 测试矩阵映射

单元（RFC 6238 附录 B 测试向量逐条断言、Base32 往返、漂移窗口）· 集成 T-TOTP-1..6（绑定→激活→202 流→恢复码消费→解绑→错误码锁定联动）· 安全（时序侧信道：错误码比较非常时→专项断言；票据暴力尝试走既有防爆破桶）· 契约（4 端点 + 202 新状态码入契约——doc_test 错误码表需扩 202/410 两个码，注意同步）· 故障注入（票据半开状态 kill → 重启后票据失效回登录）。

### 风险与回滚 / 工作量

- 风险最高项在登录流改造：以"未启用用户零回归"为第一验收门；分两轮合入（第一轮只加 setup/verify 与数据面，第二轮切登录流）。
- 回滚：`totp_enabled` 全部置 0 即回到单因素（数据保留）；登录流分支以特性开关包裹一个轮次。
- 工作量：3–4 轮（密码学基元+向量单测 1 轮；setup/管理面 1 轮；登录流切换 1 轮；全通道复验 1 轮）。

### 参考

glewlwyd `src/scheme/otp.c` + `otp.sqlite.sql`（C 语言 TOTP/HOTP scheme 与表结构，已验证）；OTPClient `src/common/`（C 生成端）；RFC 6238 附录 B（测试向量来源）。
