# 实施检查单
- [x] 固定依赖与编译闭环（依赖版本与校验值见 docs/dependencies.lock.json，正式版/测试版双构建通过）
- [x] 数据模型和初始化（--seed --init-only 预置 3 实验室、14 天场次、admin 与 user01–user20）
- [x] 登录权限与页面（libsodium 密码哈希、Cookie 会话、CSRF 与 Origin 校验，浏览器验收通过）
- [x] 预约候补事务（BEGIN IMMEDIATE 短事务、取消与补位同事务、禁用候补跳过）
- [x] 请求去重和故障测试（请求编号持久化重放；两类中断点各 10 次恢复实验通过）
- [x] 管理页面与发布（实验室维护、14 天内场次批量发布、全体记录与操作日志）
- [x] 自动测试及浏览器验收（tests/integration.py --full 全部通过，720 次并发请求 + 20 次中断恢复，见 docs/TEST_REPORT.md）
- [x] 运行说明与交付归档（README.md、docs/evidence/ 实验证据、git 提交）

# 工程深化（第二轮）
- [x] 替代时段提示：预约撞满返回同实验室 7 天内最多 3 个空闲时段，页面一键改约（T13 + 浏览器端到端验证）
- [x] 管理员统计：按日期区间（≤31 天）逐日汇总开放场次/有效预约/已取消/候补人数（T14）
- [x] 会话生命周期：过期会话 401，登录与启动时自动清理（T15）
- [x] Unity 单元测试 13 个（纯函数/数据库约束/业务层直调），随构建自动运行
- [x] gcc -fanalyzer 静态分析零告警
- [x] 加固构建（FORTIFY_SOURCE=3 + 栈保护 + 变量零初始化）全量实验复验通过；工具链无 ASan 运行库的限制已在报告中如实记录

# 功能补全（第三轮）
- [x] 数据模型升级到 user_version=2：reservations 增加 checked_in_at/cancel_reason，sessions 增加 created_at，新增 notifications 表；按列存在性幂等迁移，已用构造的 v1 库验证升级后数据完整、历史取消原因回填为 USER
- [x] 签到与爽约管理：场次开始后限时签到（可配置窗口），超时未签到由服务端扫描线程在单事务内释放名额并按 FIFO 补位；补位预约以补位时刻为签到起点，避免循环释放
- [x] 补位与爽约通知：站内通知列表、未读数、单条与全部标记已读，随补位事务一并提交
- [x] 改密与在线会话管理：自助修改密码（其他会话立即失效）、会话列表与强制下线
- [x] 记录分页与统计导出：我的记录与管理记录支持 page/page_size/has_more；统计含爽约与已签到计数并可导出 CSV
- [x] 单元测试扩至 16 个（新增签到业务、通知、会话、改密、爽约扫描与分页参数）
- [x] 集成实验扩至 24 项（新增 T16–T22），正常构建全量实验全部通过（720 次并发请求 + 20 次中断恢复）
- [x] 浏览器端到端验收：真实 Chromium 走通签到、通知、会话管理、分页与 CSV 导出，截图存证 docs/evidence/ui-r3/
- [x] 文档同步：CONTRACT / README / TEST_REPORT / tests README 已更新

# 接手核验与论文阶段（第四轮）
- [x] 接手核验：c914495 提交与远端推送属实；本地复跑通过——构建 + 单元测试 16/16、全量集成 25 项全绿（720 次并发零重复占用、20 次中断恢复正确）、-fanalyzer 零告警、加固构建 16+全量全绿
- [x] 论文大纲与图表清单产出（docs/thesis/论文大纲.md），待确认后展开正文
# 第七轮：论文与答辩交付
- [x] 论文插图 6 张（matplotlib 中文绘制，docs/thesis/figures/）
- [x] 论文初稿 docx：摘要（中英）+ 7 章 + 参考文献 + 致谢 + 附录，约 1.34 万字，4 表 6 图，实验数据全部对账（docs/thesis/thesis_gen.py）
- [x] 答辩演示 PPT 13 页（docs/thesis/答辩演示.pptx，ppt_gen.py）
- [x] 答辩现场演示脚本（docs/thesis/答辩演示脚本.md，8 分钟走查 + 应急预案）
- [ ] 学校格式模板套排（待模板）与论文定稿修改（待导师意见）


# 第五轮：双智能体并发升级（容量制/限流/指标/CI）
- [x] WorkBuddy 分支 r5/wb：CI 门禁（.github/workflows/ci.yml + README 徽章）、tests/benchmark.py 性能基准实验、src/metrics.c 运行指标（原子计数器 + 延迟直方图）、GET /api/admin/metrics 与管理端指标面板、T25
- [x] ZCode 分支 r5/core 分块④：src/ratelimit.c 登录防爆破与写操作令牌桶限流（纯函数可单测），429 LOGIN_LOCKED/RATE_LIMITED，四个新 CLI 参数，T23/T24 与限流纯函数单测
- [x] ZCode 分块⑤：容量制 schema v3（slots.capacity、退役单占用唯一索引、promote_fill 连续补位、publish capacity、前端已约 X/Y），T26 与容量/迁移单测
- [x] 修复三个实测缺陷：publish_slots 变参宽度错位（int 经 'i' 绑定致 CHECK 失败）、RateBucket 哨兵与时间戳 0 碰撞（t=0 边界单测暴露）、T26 测试自身丢失预约编号
- [x] 合并与集成验证：解决 3 处行级冲突；单元 20/20；集成 29 项全绿（正常+加固各一轮，720 次并发零重复占用、20 次中断恢复正确）；-fanalyzer 零告警；浏览器端到端通过（容量显示/替代时段/指标面板），截图 docs/evidence/ui-r5/
- [ ] 论文正文初稿（待大纲确认）

# 第六轮：深度优化
- [x] L1 性能：每线程连接复用 + 预编译语句 LRU 缓存；基准前后对比（读吞吐 +40%~+600%，p50 -86%）与 T27 回归
- [x] L4 可靠性：故障注入第三类 sweep-mid（T11 三类）、语句看门狗 5 秒墙钟打断、tests/soak.py 浸泡实验
- [x] L3 可观测性：src/log.c 分级日志 + 轮转 + 访问日志 + 慢请求告警（--slow-ms）
- [x] L5 安全：X-Frame-Options/CSP/Referrer-Policy 响应头；--backup 在线备份（SQLite Backup API）
- [x] L6 质量：tests/fuzz.py 模糊稳健性实验（240 次）；gcov 覆盖率（全模块 84.6%~98%，GCOV_PREFIX 绕行非 ASCII 路径）
- [x] L2 CI：MSYS2 ASan 内存安全通道（观察期，稳定后转必过门禁）
- [x] 文档同步：CONTRACT/README/CHANGELOG/TEST_REPORT/论文大纲


