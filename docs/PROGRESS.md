# 实施检查单
- [x] 固定依赖与编译闭环（依赖版本与校验值见 docs/dependencies.lock.json，正式版/测试版双构建通过）
- [x] 数据模型和初始化（--seed --init-only 预置 3 实验室、14 天场次、admin 与 user01–user20）
- [x] 登录权限与页面（libsodium 密码哈希、Cookie 会话、CSRF 与 Origin 校验，浏览器验收通过）
- [x] 预约候补事务（BEGIN IMMEDIATE 短事务、取消与补位同事务、禁用候补跳过）
- [x] 请求去重和故障测试（请求编号持久化重放；两类中断点各 10 次恢复实验通过）
- [x] 管理页面与发布（实验室维护、14 天内场次批量发布、全体记录与操作日志）
- [x] 自动测试及浏览器验收（tests/integration.py --full 全部通过，720 次并发请求 + 20 次中断恢复，见 docs/TEST_REPORT.md）
- [x] 运行说明与交付归档（README.md、docs/evidence/ 实验证据、git 提交）
