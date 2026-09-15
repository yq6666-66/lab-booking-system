# WorkBuddy 任务简报——r22 工程优化轮（资源管理与预约候补一体化）

> 交接方：ZCode（主分支 r9/core，HEAD 5c56702，v1.10.0）
> 工作方式：你在新分支 `r22/wb` 上开发，完成后推送，由 ZCode 审核合并集成。
> 本文档自包含：不读记忆、不问历史，所有需要知道的内容都在这里。

---

## 1. 项目是什么

**基于 C 语言的实验室资源管理与预约候补一体化系统**（软件工程毕业设计）。
单进程 C11 Web 服务：CivetWeb（嵌入式 HTTP）+ SQLite（WAL）+ cJSON + libsodium（Argon2id），前端为无框架原生 HTML/JS/CSS。核心业务：实验室场次发布、容量制预约、FIFO 候补自动补位、限时签到/签退、爽约信用、资源（设备）清单与声明配额、审批流、统计导出、Prometheus 指标。

- 课题名：基于 C 语言的实验室资源管理与预约候补一体化系统设计与实现
- 仓库：github.com/yq6666-66/lab-booking-system
- 当前版本：v1.10.0（21 轮迭代，HEAD 5c56702）

## 2. 当前状态（你的起点）

| 维度 | 数值 |
|---|---|
| 集成断言组 | 52 项全绿（T01–T51 + CLI 2 项） |
| 契约测试 | 36 端点 + 14 错误场景 |
| 单元测试 | 24 个 |
| E2E | 6 用例（Playwright） |
| CI | 三通道（构建+集成 / 静态分析 / ASan）全绿 |

业务规则编号已用至 **BR14**；schema 版本 **user_version=3**（结构检测式幂等迁移，版本号不再递增）。

## 3. 环境与命令

```bash
# 分支（从远程 r9/core 拉新分支）
git fetch origin && git checkout -b r22/wb origin/r9/core

# 构建（Release + TEST_FAULTS + 单元测试，全绿才输出 OK）
powershell -File scripts/build.ps1

# 快速集成（约 2 分钟）
LAB_TEST_PASSWORD='Test-Lab-2026' python tests/integration.py --quick

# 全量集成（含 720 并发争抢 + 30 次故障注入，约 10 分钟）
LAB_TEST_PASSWORD='Test-Lab-2026' python tests/integration.py --full

# 契约测试
LAB_TEST_PASSWORD='Test-Lab-2026' python tests/contract_test.py

# 本地演示服务（端口 8080，预置 admin/user01–20 + 10 天历史数据）
./build/lab-booking.exe --db data/demo.db --port 8080
# 演示账号：admin / user01–20，密码均为 Demo-Lab-2026
```

Python 依赖：仅标准库 + Playwright（E2E）。无 pip 需求。

## 4. 代码地图

```
src/main.c       CLI 解析、seed/demo 数据、serve 入口
src/http.c       CivetWeb 接入、路由分发、会话/CSRF/限流、/metrics
src/service.c    全部业务逻辑（预约/候补/签到/审批/令牌/资源/统计）
src/db.c         schema、幂等迁移、种子、备份轮转、完整性检查
src/util.c       result 封套/jstr/jid/hash/parse_id 等纯函数
src/log.c        分级日志（data/logs/app.log）
src/metrics.c    原子计数 + 延迟直方图（Prometheus 端点数据源）
src/ratelimit.c  令牌桶 + 登录锁定
web/             前端（index.html 用户端 / admin.html 管理端 / app.js / admin.js / style.css）
tests/           integration.py（52 项）/ contract_test.py（36 端点）/ e2e_r10.py / unit.c
docs/            CONTRACT.md（接口契约）/ CHANGELOG / PROGRESS / TEST_REPORT / handoff/
scripts/         build.ps1 / coverage.ps1 / start-demo.ps1
```

## 5. 硬规则——血泪教训清单（违反任意一条都会踩坑）

1. **db_run 绑定格式串**：`'i'` 按变参 **8 字节（Id）** 读取——绑定参数必须是 `Id` 类型，**禁止传 int**（`(int)` 强转即触发变参宽度错位）。参数个数、顺序必须与 `?` 占位符严格一一对应（已有 6 例同族缺陷：段错误/静默失败）。
2. **db_rows/db_first 的 `_id` 后缀列是 JSON 字符串**（内部用 jid 序列化）：`valuedouble` 取值恒为 0。取值用 `parse_id(jstr(row,"col"),&out)`；布尔类列（enabled 等）输出 JSON bool；普通整型列输出 number。
3. **新增列一律走幂等迁移块**：`if(!has_column(d,"表","列")){BEGIN;ALTER;错误检查;COMMIT;}`。改 CHECK 约束必须重建表：`PRAGMA foreign_keys=OFF` → BEGIN → 建新表 → INSERT SELECT → DROP → RENAME → 重建索引 → COMMIT → `PRAGMA foreign_keys=ON`（注意：事务内的 foreign_keys PRAGMA 是 no-op，必须放在 COMMIT 之后）。
4. **stmt 缓存会保留旧 schema 语句**：ALTER 后的 has_column 检测要警惕缓存污染——如果行为诡异，先怀疑这里。
5. **cJSON 判定布尔列**：db_rows 输出的整型列是 number，`cJSON_IsTrue(number)` 恒为 false——用 `obj->type==cJSON_Number && (int)obj->valuedouble==1` 判定。
6. **API 响应统一 `result(status,code,message,data)` 封套**；语义错误码用大写蛇形（STATE_CONFLICT / TIME_CONFLICT / ASSET_QUOTA / WEEKLY_QUOTA / LEAD_TIME / PENALTY_ACTIVE / APPROVAL_CAPACITY…）。
7. **所有写操作经 booking() 的事务协议**（BEGIN IMMEDIATE → 校验 → 写入 → 回执 → COMMIT），先校验后落库（先 INSERT 后校验会"占位后 409"）。
8. **tests/integration.py 的 Client 注册后必须 `c.csrf=resp["data"]["csrf_token"]`**，否则 POST 全 403。
9. **新端点必须同步 tests/contract_test.py 的 SCHEMAS**，且契约准备期选取的测试数据要避开其他场景的占用/重叠。
10. **db_check 是完整性门禁**：新增状态/列时同步补不变量语句（参考 checked_out_at 的两条）。
11. **文档四件套同步**：CONTRACT.md / CHANGELOG.md / PROGRESS.md / README.md；版本号在 src/app.h 的 LAB_VERSION。
12. **构建前杀残留进程**：`powershell "Get-Process lab-booking* | Stop-Process -Force"`；若增量编译行为诡异（mtime 漏检），直接删相关 .o 重编。
13. **heredoc 里写 C 转义序列（\n 等）会被写成真实换行**，破坏源码——写 C 代码用编辑器工具，不用 shell 内联。
14. **演示服务运行在 8080（data/demo.db）**，不要动它；测试一律用独立临时端口与临时库。

## 6. 已实现能力清单（避免重复造轮子）

对标 LibreBooking / Cal.com / Booked Scheduler 已落地的：容量制预约、FIFO 候补补位、限时签到+签退（实机时口径）、爽约信用（BR10）、时间重叠（BR11）、资源声明配额（BR12）、每周配额（BR13）、预约提前量（BR14）、周期性发布（weekdays 掩码）、审批流（PENDING/approve/reject）、API 令牌管理、资源清单+声明配额+利用率（场次与实机时双口径）、提醒、备份轮转+恢复、操作审计、分级日志、限流防爆破、Prometheus /metrics、iCalendar 导出、CSV 导出（统计/利用率）、记录状态筛选、通知分页。

## 7. 本轮候选优化方向（按价值排序，选 2–4 项做深做透）

以下均为 GitHub 对标项目已验证、且本工程**尚未实现**的能力。每项都标注了工作量与涉及文件：

1. **管理端"待审批"专页签**（中）：r19 的审批目前藏在全员记录里。在 admin.html 增加独立页签：待审批列表（资源/用户/时段/备注）、批准/拒绝按钮、待审批计数进概览卡片。涉及 web/admin.html+admin.js、src/http.c（可复用 records?status=PENDING）、src/service.c。
2. **利用率报表 CSV 导出**（小）：r21 的声明占用表（/api/admin/asset-claims）目前只有页面展示，加 `GET /api/admin/asset-claims/export` 输出 BOM CSV（对齐 stats/export 模式）。
3. **Webhook 事件通知**（大，慎选）：预约/取消/审批事件 POST 到 labs 配置的回调 URL（CivetWeb 的 mg_download 做 HTTP 客户端）。对标 Cal.com webhooks。风险：出站网络调用在回环部署下无真实消费者，演示价值打折。
4. **X-API-Token 只读访问真正启用**（中）：r20 的令牌目前只能管理（创建/列表/吊销），未接入认证路径。在 dispatch 的 GET 分支支持 `X-API-Token` 头等免 Cookie 认证（只放行 GET），管理端/用户端令牌面板已就绪。
5. **操作日志导出 CSV**（小）：GET /api/admin/logs/export，对齐 stats/export 模式。
6. **预约冲突/占用日历视图**（大，前端为主）：管理端按实验室展示周视图占用格子。纯前端工作量集中，无后端改动。

> 选择原则：**做深做透 2–4 项 > 浅尝 6 项**。每项必须有集成断言组（tests/integration.py 新 T 编号）+ 契约条目（新端点）+ 文档同步。

## 8. 验收标准（ZCode 集成时逐条检查）

- [ ] `powershell scripts/build.ps1` 全绿（24 单元 + 三目标构建）
- [ ] `--quick` 集成 ≥52 项全绿（新增功能须有对应新 T 断言组）
- [ ] `--full` 全量回归全绿（含并发争抢与故障注入）
- [ ] 契约测试 ≥36 端点全绿（新端点已入 SCHEMAS）
- [ ] 24 单元测试全绿；db_check 无新增违规
- [ ] CONTRACT.md / CHANGELOG.md / PROGRESS.md / README.md 已同步；LAB_VERSION 已按语义递增（建议 1.11.0）
- [ ] 未修改 tests/integration.py 既有断言语义（只允许新增）
- [ ] 未引入任何第三方依赖；前端保持无框架原生 JS
- [ ] git 提交信息清晰（feat:/fix: 前缀），推送至 `r22/wb` 分支

## 9. 交付与集成流程

1. 在 `r22/wb` 分支完成开发与自测，`git push origin r22/wb`。
2. 在分支最新提交上附一段交付说明：做了什么、新增端点/表/列、测试增量、已知限制。
3. ZCode 将：审核代码与证据 → 在本地复跑全量验收 → 合并进 r9/core 与 main → 跑 CI 三通道确认。
4. 集成期间发现的问题会回推到 `r22/wb` 由你修复（保持分支活跃至合并完成）。

## 10. 明确不做（与 ZCode 已有取舍一致）

QR 码签到（C 端 QR 编码无依赖实现成本过高）；SMTP 邮件（出站依赖重）；Google/Outlook 日历 OAuth（外部服务依赖）；多租户（超课题范围）。
