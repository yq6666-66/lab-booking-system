# 实验室资源管理与预约候补一体化系统

[![CI](https://github.com/yq6666-66/lab-booking-system/actions/workflows/ci.yml/badge.svg)](https://github.com/yq6666-66/lab-booking-system/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/release-v1.17.0-blue)](https://github.com/yq6666-66/lab-booking-system/releases/tag/v1.17.0)
![language](https://img.shields.io/badge/language-C11-blue)
![license](https://img.shields.io/badge/license-MIT-green)

高校开放实验室的一站式资源管理平台——**预约、候补、签到、审批、信用、公平审计**全闭环，纯 C 实现。

> **技术核心**：单写者事务保证并发零超卖 · 持久化回执实现幂等重试 · 故障注入验证崩溃恢复 · 三档候补策略（含老化加权）· Jain 公平指数量化治理

---

## 核心能力

### 预约与候补

| 能力 | 说明 |
|---|---|
| 容量制预约 | 场次 1–200 席位，实时显示"已约 X/Y" |
| 三档候补策略 | `strict` / `executable`（默认，暂跳冲突保留序号）/ **`weighted`** 老化加权 |
| 老化加权评分 | score = 1000×优先级 + 200×(信用−5) + 60×log₂(等待小时)——等待翻倍 +60 分，消除同档饿死 |
| HELD 限时保留 | `--hold-window` 启用后递补先落 HELD，用户截止前一键确认 |
| 知情候补 | 建议面板逐场次解释"可约/可候补"与原因，附**历史转正概率**（5 周经验分布） |
| 替代时段 | 撞满时返回同实验室 7 天内空闲场次，页面一键改约 |
| 原子改期 | 同实验室改期在单事务内完成新槽校验+迁移+旧槽补位 |
| 每日候补上限 | `--waitlist-daily-limit`（北京日界重置） |

### 审批与信用

| 能力 | 说明 |
|---|---|
| 预约审批流 | 实验室可开启 `require_approval`，新预约落 PENDING 不占容量，批准时事务内重校 |
| 批量审批 | 部分成功语义——容量满条目入 `failed[]` 不影响其余 |
| 信用账户 | 签到 +1 / 被抢占补偿 +1 / 爽约额外 −1 / 每周回补至基准 5 |
| 优先级抢占 | 管理员满员时可抢占未签到且优先级更低者，补偿 +1 信用 |
| 管理员强制操作 | 「强制取消」（ADMIN 原因+FIFO 递补+通知）/「代签退」（保实机时） |

### 资源管理

| 能力 | 说明 |
|---|---|
| 资源声明配额 | 预约可声明 ≤5 项设备，同时段配额事务内校验（BR12） |
| 维护工单 | 开/关工单自动通知受影响声明持有者（`affected` 计数） |
| 可用时段窗 | 按星期+时刻配置设备可用窗口 |
| 资格授权 | 设备可开启"需资格"开关，管理员授予/撤销 |
| 利用率统计 | 场次口径 + **实机时口径**（签到-签退时长聚合） |

### 公平与治理

| 能力 | 说明 |
|---|---|
| 公平性审计 | 按用户聚合候补入队/转正/退出/平均等待，全站 **Jain 公平指数** |
| 时间重叠检测 | 同用户有效预约在时间轴上不得重叠 |
| 爽约限制 | 近 7 天爽约 ≥2 次限制新预约，窗口滑动自动恢复 |

### 运营与分析（v1.17.0）

| 能力 | 说明 |
|---|---|
| 聚合概览 | `GET /api/admin/dashboard`——今日统计+待审批+候补热点+七日趋势一次返回，替代前端串行 5 请求 |
| 场次冲突检测 | `GET /api/admin/slots/conflicts`——同日跨实验室时间重叠排查（排课避撞） |
| 信用健康概览 | `GET /api/admin/credit-summary`——五档分布+受限用户名单+最近流水 |
| 通知历史导出 | `GET /api/me/notifications/export`——个人通知 CSV（BOM，Excel 直开） |

### 前端体验（v1.17.0）

- **深色主题**：`prefers-color-scheme` 自适应 + 手动切换，**View Transitions 圆形扩散过渡**
- **共用基座 ui.js**：表单校验、分页、通知面板、多行文本截断
- **管理台重构**：认证门（#gate）、12 页签、待审批专页、强制操作按钮
- **视觉与动效七轮打磨**：毛玻璃吸附顶栏、入场错峰与弹簧按压、等宽数字防跳动、危险操作 3 秒倒计时确认、移动端吸顶页签+16px 防缩放、favicon 冒泡动画——全部令牌化并尊重 `prefers-reduced-motion`
- **无障碍**：`prefers-reduced-motion` 尊重、表单无 JS 安全降级

---

## 快速开始

```powershell
# 构建（vendor 依赖已入库，无需联网；自动运行 24 个单元测试）
powershell -File scripts/build.ps1

# 一键启动演示（首次自动初始化，浏览器访问 http://127.0.0.1:8080）
powershell -File scripts/start-demo.ps1 -Password 'Demo-Lab-2026'
```

| 演示账号 | 说明 |
|---|---|
| `admin` | 管理员（12 页签控制台 `/admin.html`） |
| `user01` ~ `user20` | 普通用户（预约/候补/签到/通知/令牌） |

> 部署：`lab-booking.exe` + `libsodium-26.dll` + `web/` 三件套即可迁移；`tests/install_test.ps1` 在空目录完整演练。

管理员登录走独立入口（登录页双 Tab），普通用户走管理员入口得 403 不建立会话。

---

## 测试矩阵

| 通道 | 规模 | 结果 |
|---|---|---|
| 集成实验 | 79 项断言（T01–T78 + CLI） | ✅ 全绿 |
| 并发争抢 | 720 次（1/5/10/20 并发 × 20 轮） | ✅ 零超卖 |
| 故障注入 | 30 次（三类中断点 × 10 轮） | ✅ 恢复正确 |
| 契约测试 | 56 端点 + 14 错误场景 | ✅ |
| E2E 浏览器 | 8 用例（Playwright） | ✅ |
| 单元测试 | 24 用例（Unity） | ✅ |
| 浸泡稳定性 | 60s + 300s（51,140 请求） | ✅ 零错误 |
| 模糊稳健性 | 240 次恶意输入 | ✅ 零 5xx |
| 文档一致性 | 7 项 | ✅ |
| 安装部署 | 8 步全流程 | ✅ |
| CI 门禁 | 四通道（构建/契约/静态分析/ASan） | ✅ |

```powershell
python tests/integration.py --quick    # 快速回归（~1 分钟）
python tests/integration.py --full     # 完整实验（并发+故障注入）
python tests/benchmark.py              # 性能基准
```

详细报告：[docs/TEST_REPORT.md](docs/TEST_REPORT.md) · 断言清单：[tests/README.md](tests/README.md)

---

## 系统架构

```
浏览器（原生 HTML/JS/CSS · ui.js 基座 + theme.js 深色主题）
   │  JSON over HTTP（回环地址 127.0.0.1）
   ▼
CivetWeb HTTP 服务（8 工作线程 · #gate 认证门 · CSRF/Origin/Host · 限流）
   ▼
业务层 service.c（通知渠道+outbox · 会话/用户/扫描）+ booking.c（预约/候补/审批）
   │         asset.c（资源/时段/维护） stats.c（统计/导出） token.c（令牌/信用）
   │         ratelimit.c（令牌桶）   metrics.c（计数+延迟直方图）   log.c（分级+轮转）
   ▼
数据访问 db.c（参数绑定 · 语句 LRU 缓存 · 线程连接复用 · schema v5 幂等迁移）
   ▼
SQLite（WAL · synchronous=FULL · 外键 · 17 表 · 容量不变量）
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [答辩技术手册](docs/答辩技术手册.md) | 架构深挖 · 代码导航 · 实验数据 · 高频问题预答 |
| [接口契约](docs/CONTRACT.md) | 56 端点 · 数据模型 · 事务规则 · 组合语义基准 |
| [测试报告](docs/TEST_REPORT.md) | 实验设计 · 数据 · 结论 |
| [UAT 验收](docs/UAT.md) | 六路径走查 · 发布回归清单 |
| [变更日志](docs/CHANGELOG.md) | 16 个版本 · 42 轮迭代 |
| [断言清单](tests/README.md) | T01–T78 全量描述 |
| [证据归档](docs/evidence/) | 基准结果 · 验收截图 · 实验数据 |

---

## 目录结构

```
src/          C 源码（main · util · db · service · http · ratelimit · metrics · log）
web/          前端（index · admin · app · admin · ui · theme · style · favicon）
vendor/       第三方依赖源码（已入库，克隆即可构建）
scripts/      构建 · 依赖获取 · 演示启动 · 混合负载实验
tests/        集成实验 · 契约 · 单元 · E2E · 基准 · 浸泡 · 模糊 · 属性测试
docs/         契约 · 测试报告 · 进度 · 答辩材料 · 论文 · 证据
data/         运行数据库（.gitignore）
```

---

## 第三方组件

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [CivetWeb](https://github.com/civetweb/civetweb) | commit 588860e | MIT | 嵌入式 HTTP 服务 |
| [SQLite](https://www.sqlite.org/) | 3.53.4 | Public Domain | 嵌入式数据库 |
| [cJSON](https://github.com/DaveGamble/cJSON) | 1.7.19 | MIT | JSON 解析 |
| [libsodium](https://libsodium.org/) | 1.0.22 | ISC | Argon2id 密码哈希 |
| [Unity](https://github.com/ThrowTheSwitch/Unity) | 2.7.19 | MIT | 单元测试框架 |

固定版本与 SHA256 见 [docs/dependencies.lock.json](docs/dependencies.lock.json)。

---

## 许可证

[MIT License](LICENSE) · 第三方组件沿用各自许可证（见上表）

---

*本项目为软件工程毕业设计，42 轮迭代全部由自动化测试驱动。*
