# 独立集成与可靠性实验

`integration.py` 使用 Python 标准库，测试客户端不属于 C 后端程序。所有数据库都由测试创建在项目 `artifacts/test-runs/` 的唯一实验目录，服务器只使用独立回环端口；脚本不操作用户运行数据库。异常中断只针对脚本启动的测试进程。每轮实验的原始数据库及日志保留，脚本不自动删除；SQLite 查询使用显式关闭连接，避免 Windows 文件句柄残留。

## 执行

先完成主程序和启用 `TEST_FAULTS` 的测试程序构建，再在项目根目录执行：

```powershell
powershell -File scripts/build.ps1                     # 构建正式版/测试版并运行单元测试
python tests/integration.py --quick --output tests/results-quick
python tests/integration.py --full --output tests/results-full
powershell -File scripts/build.ps1 -Analyze            # 追加 gcc -fanalyzer 静态分析
powershell -File scripts/build.ps1 -Harden             # FORTIFY+SSP 加固构建（*-harden.exe）
python tests/integration.py --full --exe build/lab-booking-harden.exe --test-exe build/lab-booking-test-harden.exe --output tests/results-full-harden
```

`--exe`、`--test-exe` 可以指定不同的已构建程序。输出目录请每次采用新的名字以保留既有实验；默认 `tests/results`。测试口令不入库：运行前设置环境变量 `LAB_TEST_PASSWORD`（至少 8 位，脚本启动时校验），该口令经 `LAB_SEED_PASSWORD` 传给初始化子进程。脚本不会把测试登录口令写入证据。`scripts/fetch_dependencies.py` 仅允许访问 `docs/dependencies.lock.json` 固定域名的 HTTPS 地址（下载前校验 scheme 与主机白名单，并核对 SHA256）。

单元测试（`build/unit-tests.exe`，随 `build.ps1` 自动构建运行）基于 Unity 框架，覆盖纯函数（ID/UUID/日期/哈希/十六进制令牌/分页参数）、数据库唯一索引与启动检查，以及不经 HTTP 直接调用业务层 `booking()`、`notify()`、`sessions_list()`、`session_revoke()`、`password_change()`、`sweep_once()` 的 16 个用例；证据输出在 `docs/evidence/unit-tests.txt`。本 MinGW 工具链不含 libasan/libubsan 运行库，动态内存检查不可用，改以 `-fanalyzer` 静态分析加 `_FORTIFY_SOURCE=3`、栈保护、自动变量零初始化的加固构建跑全量实验替代，并在报告中如实说明。

`--quick`：每个并发档位一轮，每种故障一轮；`--full`：1、5、10、20 个并发用户各 20 轮，共 720 次预约请求；两个故障点各 10 轮，共 20 次中断恢复实验。所有实验使用固定请求数量而非固定时间压测。每轮并发使用一个未预约场次；每次故障采用全新数据库。签到、通知、会话、分页与导出（T16–T22）在独立服务器实例上执行：签到与爽约实验使用 `--checkin-window 1 --sweep-interval 1` 以获得确定性，其余使用默认窗口。

## 断言范围

| 编号 | 测试内容 |
| --- | --- |
| T01 | 登录、匿名拒绝、本人信息、实验室及管理记录查询 |
| T02 | 一个场次一次有效预约、冲突响应 |
| T03 | FIFO 候补、退出后重新入队排在末尾、连续补位 |
| T04 | 同编号同内容重放、同编号换参数冲突、重复取消不影响新预约 |
| T05 | 他人记录权限、管理员权限、CSRF 和跨来源请求 |
| T06 | 非法 JSON、超过 16384 字节、字段类型、非法日历日期 |
| T07 | 已开始场次禁止新操作，原请求仍可重放 |
| T08 | 外部写锁导致约 3 秒后返回忙碌，释放锁后原编号可成功重试 |
| T09 | 禁用账号候补被跳过，下一位有效候补获得名额 |
| T10 | 并发独立连接抢占同场次，HTTP 成功与数据库占用均为 1 |
| T11 | 事务提交前退出 86、提交后响应前退出 87；重启状态、重试与补位 |
| T12 | SQLite 完整性、外键、有效占用与有效候补唯一性 |
| T13 | 预约已满返回替代时段：同实验室、7 天内、升序、≤3 个、与 SQL 对账一致，且首个替代可直接预约 |
| T14 | 管理统计总额与逐日数据同 SQL 对账；非管理员 403、非法日期/超 31 天/缺参数 400 |
| T15 | 会话过期后请求 401；重新登录触发过期会话清理 |
| T16 | 签到成功、同编号重放与重复签到均返回首次签到时间、非本人签到被拒、已签到不被判爽约 |
| T17 | 场次尚未开始与签到窗口已过均返回 409 STATE_CONFLICT，不允许补签 |
| T18 | 我的记录与管理记录分页（page/page_size/has_more），非法 page=0 与超大 page_size 返回 400 |
| T19 | 统计含爽约/已签到计数；CSV 导出带 BOM、中文表头与合计行；缺参数 400、非管理员 403 |
| T20 | 改密：旧密码错误 401、过短或与原密码相同 400；成功后其他会话立即失效、当前会话保留、新密码可登录 |
| T21 | 在线会话列表与强制下线；非法会话编号与重复下线返回 404 |
| T22 | 签到超时自动释放为 NO_SHOW、名额按 FIFO 补位给候补、预约人与候补人各收到通知、通知可单条或全部标记已读 |

此外检查 `--check` 和正常程序拒绝故障参数。测试中对时间及用户启用状态直接修改**测试数据库**，目的是构造确定边界；并发预约与候补操作均通过实际 HTTP 接口。

## 证据

- `results.json`：测试时间、模式、各项断言结果及失败原因。
- `concurrency.csv`：每轮并发数、成功数、占用数、冲突与忙碌数、吞吐量和响应时间。
- `logs/`：测试服务器诊断日志。
- `artifacts/test-runs/quick-*` 或 `full-*`：对应实验完整原始数据库与日志，路径写入 `results.json`，失败时也保留。

p50 为本轮响应时间中位数，p95 为最近秩法第 95 百分位。单轮仅 1～20 个请求，p95 接近最大值；这些数字只描述本机指定条件，不代表生产容量。吞吐量包括线程调度及客户端连接开销。

退出码 0 表示脚本记录的所有断言通过；1 表示有失败。初始化、服务无法启动等前置失败可直接导致 Python 异常退出，应先解决后重新运行。不得将语法检查通过或尚未运行的脚本作为业务测试通过证据。
| T23 | 同一用户名连续错误登录达阈值后正确密码也返回 429 LOGIN_LOCKED；其他账号不受影响；锁定到期自动解锁 |
| T24 | 已登录用户写操作超过 --rate-burst 后返回 429 RATE_LIMITED；补充窗口后恢复 |
| T26 | 发布 capacity=3 场次：第 4 人 409 满员、列表容量与计数一致、取消触发 FIFO 补位、已约者再约 ALREADY_RESERVED、满员可候补 |
