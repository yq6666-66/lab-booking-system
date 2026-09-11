# 测试覆盖矩阵

> 本文档映射软件工程标准测试分类到本项目的具体测试载体、用例编号与证据文件。
> 最后更新：2026-09-11（第九轮）。

## 测试分类覆盖矩阵

| # | 测试类型 | 载体 | 用例/脚本 | 状态 | 证据 |
|---|---------|------|----------|------|------|
| 1 | **单元测试** | tests/unit.c → build/unit-tests.exe | 24 用例（纯函数/DB 不变量/业务直调/限流纯函数/缓存/迁移/**边界值**） | ✅ 全过 | docs/evidence/unit-tests.txt |
| 2 | **集成测试** | tests/integration.py | T01–T31 共 33 项断言组 | ✅ 全过 | docs/evidence/results-full-merge/ |
| 3 | **系统测试（E2E）** | Playwright 驱动 Chromium | 三轮走查（登录/预约/候补/签到/通知/导出） | ✅ 通过 | docs/evidence/ui-r3/, ui-r5/ |
| 4 | **验收测试（UAT）** | docs/test/UAT_scenarios.md | 8 个正式验收场景 | ✅ 脚本就绪 | 本文档 |
| 5 | **性能测试** | tests/benchmark.py + soak.py | E1 读性能 + E2 写争用 + 浸泡 60s | ✅ 通过 | docs/evidence/benchmark/, soak/ |
| 6 | **安全测试** | T23/T24/T05 + fuzz.py + **security_test.py** + 限流/防爆破/CSRF | 限流/防爆破/越权/注入/CSRF/Origin/**SQL 注入 20 载荷**/**时序侧信道** | ✅ 全过 | results-full-merge/, fuzz/, security_results.json |
| 7 | **白盒测试** | unit.c 直调内部函数（不做 HTTP） | 语句缓存/线程复用/看门狗/迁移实测 | ✅ 全过 | unit-tests.txt |
| 8 | **黑盒测试** | integration.py 经 HTTP（不知内部实现） | 全部 33 项集成断言组 | ✅ 全过 | results-full-merge/ |
| 9 | **灰盒测试** | tests/graybox_test.py（WB 交付） | API 驱动 + DB 直查交叉验证 | ✅ 脚本就绪 | 运行输出 |
| 10 | **静态测试** | gcc -fanalyzer + 绑定参数核查 + 加固构建 | 全源码分析 + FORTIFY/SSP | ✅ 零告警 | 构建日志 |
| 11 | **动态测试** | 全部运行时测试（1–8、10 之外的运行时验证） | 所有需要启动服务并交互的测试 | ✅ 全过 | 各证据目录 |
| 12 | **自动化测试** | CI 三 job（GitHub Actions） | push 触发：构建+单元+快速集成+静态分析 | ✅ 运行中 | GitHub Actions |
| 13 | **手工测试** | docs/test/manual_test_cases.md | 12 个手工用例（含步骤/预期/实际/结论列） | 📋 脚本就绪 | 本文档 |
| 14 | **兼容性测试** | schema 迁移实测 + docs/BROWSER_COMPAT.md | v1→v3 迁移实测 + 浏览器矩阵声明 | ✅ 通过 | docs/BROWSER_COMPAT.md |
| 15 | **文档测试** | tests/doc_test.py（WB 交付） | README/CONTRACT 与实际 API 一致性 | ✅ 脚本就绪 | 运行输出 |
| 16 | **易用性测试** | docs/test/usability_checklist.md | Nielsen 10 原则逐项评估 | 📋 已评估 | 本文档 |
| 17 | **界面测试** | 截图存证（ui-r3/ui-r5）+ 响应式断言 | 桌面/窄屏/移动布局 | ✅ 通过 | docs/evidence/ui-r5/ |
| 18 | **安装测试** | tests/install_test.ps1（WB 交付） | 全新环境部署（构建→种子→启动→验证→清理） | ✅ 脚本就绪 | 运行输出 |
| 19 | **第三方测试** | tests/vendor_verify.py（WB 交付） | SHA256 逐项核对 vendor 组件完整性 | ✅ 脚本就绪 | 运行输出 |
| 20 | **模糊测试** | tests/fuzz.py | 240 次畸形输入，零崩溃零 5xx | ✅ 通过 | docs/evidence/fuzz/ |
| 21 | **浸泡测试** | tests/soak.py | 34970 请求 60s，工作集 +3MB，句柄 +44 | ✅ 通过 | docs/evidence/soak/ |
| 22 | **混沌工程** | tests/chaos.py（WB 交付） | 随机 SIGKILL + 完整性验证 | ✅ 脚本就绪 | 运行输出 |
| 23 | **并发压力** | tests/waitlist_stress.py | 多用户竞争候补 FIFO 公平性 | ✅ 通过 | docs/evidence/waitlist-stress/ |
| 24 | **基准对比** | docs/evidence/benchmark/before-after/ | 连接复用优化前后吞吐/延迟对比 | ✅ 完成 | comparison.md |

## 测试层次与代码覆盖

| 层次 | 工具 | 覆盖范围 |
|------|------|---------|
| 单元（白盒） | Unity + gcov | 逐函数验证，行覆盖 84.6%~98% |
| 集成（灰盒） | Python HTTP + SQLite 直查 | 接口→业务→DB 全链路 |
| 系统（黑盒） | Playwright + 真实浏览器 | 用户可见交互全路径 |
| 静态 | -fanalyzer / -Wextra / 绑定核查 | 编译期全源码 |

## 缺陷发现统计

| 轮次 | 缺陷 | 发现手段 |
|------|------|---------|
| r5 | sessions_list 绑定 "is" 格式串错位 → 段错误 | 单元测试 |
| r5 | /api/slots 绑定漏位 → 查不到场次 | 集成测试 |
| r5 | 爽约-补位死循环 | 集成测试超时 |
| r5 | 迁移回填遗漏 cancel_reason | 迁移实测 |
| r5 | 前端分页 id 拼接错误 | 浏览器验收 |
| r6 | publish_slots 变参宽度错位 → CHECK 失败 | 种子初始化 |
| r6 | RateBucket 哨兵与时间戳 0 碰撞 | t=0 边界单测 |
| r6 | T26 测试丢失预约编号 | 集成测试 |
| r7 | sessions INSERT fmt "ssiii"→"sisii" 段错误 | 独立 C 复现 + 插桩 |
| r7 | op_events fmt 漏绑 created_at | 粘滞错误覆盖 200 |
| r7 | T24 时序脆弱（限流探测） | 多轮脉冲加固 |
| r9 | 登录时序侧信道：不存在用户 ~9ms vs 有效用户 ~87ms（9.4x），可枚举用户名 | security_test.py 时序测量 |
| r9 | 修复：login() 对不存在用户执行等量 Argon2id dummy 验证，ratio 降至 1.11x | security_test.py 复测 |

## r9 安全专项：SQL 注入与时序侧信道（tests/security_test.py）

**SQL 注入抵抗**：20 个载荷（`' OR 1=1 --`、UNION SELECT、 stacked queries、
盲注时间函数等）经登录/查询端点注入，全部被参数化绑定拦截：无 500、
users 表行数不变、sqlite_master 无意外表。

**时序侧信道**：测量有效用户名(admin)+正确口令 与 不存在用户名+错误口令
的登录响应时间中位数（各 30 次）。

| 阶段 | 有效用户 | 不存在用户 | 比值 | 结论 |
|------|---------|-----------|------|------|
| 修复前 | 87.4ms | 9.2ms | 9.4x | 泄露用户存在性（短路跳过 Argon2id） |
| 修复后 | 89.3ms | 80.5ms | 1.11x | < 3.0x 阈值，通过 |

修复（src/http.c login()）：用户不存在时仍执行一次 Argon2id 验证
（首次调用时惰性生成 dummy hash，同 OPSLIMIT/MEMLIMIT_INTERACTIVE 参数），
两条路径 KDF 工作量一致，响应时间不再区分用户名是否存在。

**测试方法论修正**：初版对同一不存在用户名连发 30 次，第 6 次起命中登录
锁定（429 快速路径，1.7ms），测到的是限流而非侧信道；改为轮换不存在的
用户名并调高 --login-max-fails，确保每次测量都走到完整验证路径。
