# 已验证参考实现索引

> 所有路径均于 2026-09-21 通过 GitHub API / 仓库结构读取**实证存在**（非凭记忆引用），可直接按路径溯源。
> 检索方法与全部查询词见 [../../COMPETITIVE_ANALYSIS.md](../../COMPETITIVE_ANALYSIS.md) §1。

## 按蓝图项映射

| 蓝图项 | 参考仓库 | 已验证路径 | 参考价值 |
|---|---|---|---|
| 1.1 outbox 派发器 | [LibreBooking/librebooking](https://github.com/LibreBooking/librebooking)（809★，PHP） | `Jobs/sendwaitlist.php` · `Jobs/sendreminders.php` · `Jobs/sendmissedcheckin.php` · `Jobs/autorelease.php` · `Jobs/JobCop.php` | 候补通知/提醒/自动释放的作业化拆分与作业守护（JobCop）模式；`sendwaitlist.php` 证明候补出站通知是该领域成熟实践 |
| 1.2 OpenAPI | [alextselegidis/easyappointments](https://github.com/alextselegidis/easyappointments)（4388★，PHP） | `application/controllers/api/v1/Appointments_api_v1.php` 等 8 个 `*_api_v1.php` | REST 版本化控制器划分与资源命名；对照本项目手写契约的演进方向 |
| 1.2 OpenAPI（对照） | [sjtu-libook/libook](https://github.com/sjtu-libook/libook)（16★，Django+React） | README 载明 `/api/schema/swagger-ui/` 自动生成 | 自动生成路线对照：需要框架反射支持，本项目以受限手写 spec + 零依赖校验折中 |
| 2.1 QR 签到 | [nayuki/QR-Code-generator](https://github.com/nayuki/QR-Code-generator)（6777★，MIT） | `c/qrcodegen.c` · `c/qrcodegen.h` · `c/qrcodegen-demo.c` · `c/qrcodegen-test.c` · `c/Makefile` | 单文件 C 实现，demo 即集成样例；vendor 锁 SHA256 后引入 |
| 2.3 TOTP | [babelouest/glewlwyd](https://github.com/babelouest/glewlwyd)（433★，C，ISC） | `src/scheme/otp.c` · `src/scheme/otp.sqlite.sql` | 纯 C 认证服务器中的 TOTP/HOTP scheme 完整实现与表结构；其 `src/` 顶层另有 `metrics.c`（C 服务做 Prometheus 的又一例证） |
| 2.3 TOTP（对照） | [paolostivanin/OTPClient](https://github.com/paolostivanin/OTPClient)（557★，C，GPL-3.0） | `src/common/`（common.c/h 等） | C 生成端实现对照；注意 GPL 许可——只作行为参考，不移植代码 |
| Phase 3.1 WebSocket | 本地 vendored civetweb（commit 588860e） | `vendor/civetweb/include/civetweb.h` 中 `mg_websocket_write`/`mg_connect_websocket_client` 等 5 处 API | 已锁定版本原生支持 WebSocket，无需升级底座（已在本仓库实证） |

## 定位参考（梯队全景见对比分析报告）

- 资源调度领域标杆：LibreBooking（审批/配额/黑名单/LDAP/Docker）
- 自托管预约平台：Easy!Appointments（Google Calendar 双向同步、多语言）
- 教室/房间预约：classroombookings（223★）、neokoenig/RoomBooking（192★）
- 图书馆座位生态（中文高校）：SeatKiller（26★，武大抢座脚本——API 化需求证据）、libook（16★，SJTU）
- C Web 平台安全标杆：kore（3825★，privsep/seccomp/ACME）、facil.io（2403★）
- 排期平台体验标杆：cal.com（calcom/cal.diy，48.6k★）、rallly（5262★）

## 复跑指引

对比分析每学期建议复跑一轮：查询词已在对比分析报告 §1 列全，配合 `gh api "search/repositories?q=<词>&sort=stars&per_page=N"` 即可复现；新增高星命中按本文件表格格式增补。
