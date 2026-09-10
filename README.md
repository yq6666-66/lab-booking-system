# 基于 C 语言的实验室预约与候补系统

高校开放实验室预约与候补管理系统（毕业设计）。C11 后端 + 浏览器静态页面，提供固定一小时场次的在线预约、FIFO 候补排队、取消自动补位与业务记录查询。技术重点是并发请求下的事务一致性、重复请求去重与进程中断后的恢复验证。

## 功能概览

**普通用户**：登录、按实验室和日期查询开放场次、预约整间实验室、取消自己的预约、加入/退出候补、查看本人预约与候补记录；预约撞满时系统返回同实验室 7 天内最多 3 个空闲替代时段，页面可直接一键改约。

**管理员**（admin）：新增与维护实验室、按日期区间批量发布开放场次（每日 08:00–12:00、14:00–18:00，单次最多 14 天）、查看全体预约、候补与操作日志、按日期区间（最多 31 天）查看逐日预约统计（开放场次、有效预约、已取消、候补人数及合计）。

**核心业务规则**：

- 每个场次同一时刻最多一条有效预约，即时生效；
- 场次已满可加入候补，按成功入队顺序（`waitlist.id` 递增）排队；同一用户同一场次最多一条有效预约或候补；
- 取消预约与队首补位在同一事务内提交，外部观察不到"名额已释放但未补位"的中间状态；
- 候补者账号被禁用时自动跳过，补位给下一位有效候补；
- 每次操作携带请求编号（UUID），同编号同参数重复提交返回已保存的原结果；同编号换参数返回冲突；网络失败可用原编号安全重试；
- 场次开始后停止一切新操作，历史记录保留；
- 会话有效期 2 小时，登录与服务启动时自动清理过期会话；
- 有预约或候补历史约束的规则详见 [docs/CONTRACT.md](docs/CONTRACT.md)。

## 技术架构

| 层次 | 实现 |
| --- | --- |
| HTTP 服务 | CivetWeb（C，官方 embedded_c 示例起步），多工作线程，仅监听回环地址 |
| 业务逻辑 | 自研 C 模块（`src/service.c`）：事务、FIFO 补位、请求去重、权限 |
| 数据存储 | SQLite 3.53.4（WAL、`synchronous=FULL`、外键开启、局部唯一索引） |
| JSON 处理 | cJSON 1.7.19 |
| 安全 | libsodium 1.0.22（密码哈希、会话令牌）、Cookie 会话 + CSRF 校验、Origin 白名单 |
| 前端 | 原生 HTML/JS/CSS 静态页面（`web/`），无前端框架 |

依赖版本与校验值固定在 [docs/dependencies.lock.json](docs/dependencies.lock.json)；对上游代码的改动记录在 [docs/UPSTREAM_PATCHES.md](docs/UPSTREAM_PATCHES.md)。

## 目录结构

```
src/      C 源码（main/util/db/service/http）
web/      浏览器静态页面
vendor/   第三方依赖源码（不入库，脚本下载）
scripts/  构建与依赖获取脚本
tests/    独立集成与可靠性实验（Python 标准库）
docs/     契约、进度、测试报告、依赖清单、课题计划
build/    编译产物（不入库）
data/     运行数据库（不入库）
artifacts/ 测试原始证据与备份（不入库）
```

## 环境要求

- Windows x64
- MinGW-w64 GCC（C11）
- PowerShell（执行构建脚本）
- Python 3.9+（仅运行测试脚本，标准库即可）

## 构建与运行

```powershell
# 1. 首次获取依赖（或按 docs/dependencies.lock.json 手动放置）
python scripts/fetch_dependencies.py

# 2. 编译正式版与测试版（测试版额外支持故障注入参数），随后自动运行 13 个单元测试
powershell -File scripts/build.ps1          # 加 -Analyze 启用 gcc 静态分析；加 -Harden 输出 FORTIFY+SSP 加固版

# 3. 初始化演示数据库（密码经环境变量传入，至少 8 位）
$env:LAB_SEED_PASSWORD = '你的密码'
./build/lab-booking.exe --db data/lab.db --seed --init-only
Remove-Item Env:\LAB_SEED_PASSWORD

# 4. 启动服务并浏览器访问 http://127.0.0.1:8080
./build/lab-booking.exe --db data/lab.db --web web --port 8080
```

预置账号：`admin`（管理员）与 `user01`–`user20`（普通用户），密码均为初始化时 `LAB_SEED_PASSWORD` 设置的值。种子数据包含 3 个实验室与未来 14 天场次。

常用命令行参数：`--db FILE`、`--web DIR`、`--port PORT`（1024–65535）、`--check`（仅校验数据库完整性）。完整接口契约见 [docs/CONTRACT.md](docs/CONTRACT.md)。

## 测试

```powershell
# 单元测试随构建自动运行；也可单独执行
./build/unit-tests.exe

# 快速验证（约 30 秒）
python tests/integration.py --quick --output tests/results-quick

# 完整实验：1/5/10/20 并发各 20 轮共 720 次请求 + 两类中断点各 10 次恢复实验 + 替代时段/统计/会话测试
python tests/integration.py --full --output tests/results-full

# 加固构建（FORTIFY+SSP）全量复验
powershell -File scripts/build.ps1 -Harden
python tests/integration.py --full --exe build/lab-booking-harden.exe --test-exe build/lab-booking-test-harden.exe --output tests/results-full-harden
```

最近一轮完整实验全部通过，数据与证据说明见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)，断言范围见 [tests/README.md](tests/README.md)。

## 文档索引

- [docs/CONTRACT.md](docs/CONTRACT.md) — 接口、数据与事务契约
- [docs/TEST_REPORT.md](docs/TEST_REPORT.md) — 完整实验报告
- [docs/PROGRESS.md](docs/PROGRESS.md) — 实施进度清单
- [docs/UPSTREAM_PATCHES.md](docs/UPSTREAM_PATCHES.md) — 上游依赖改动记录
- [docs/plans/](docs/plans/) — 课题研究计划（教师版）
