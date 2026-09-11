# WorkBuddy 任务书（第九轮：契约形状 + 灰盒 + 文档测试 + 安装测试 + 第三方验证）

> 使用方法：整段"提示词"复制给 WorkBuddy 即可开工。基线 feat/c-booking 最新提交。

---

## 提示词（原样复制）

接手 C 语言实验室预约系统第九轮升级：全分类深度测试体系中的四个分块。项目在 `C:\Users\admin\Desktop\毕业设计`（git 仓库，分支 feat/c-booking，基于最新提交工作）。

**第 0 步 环境**：`git worktree add C:\Users\admin\Desktop\r9-wt-wb -b r9/wb feat/c-booking`，后续只在 `C:\Users\admin\Desktop\r9-wt-wb` 工作。禁止推 main、禁止改 feat/c-booking、禁止 rebase。同一文件多处修改必须串行编辑。

**文件白名单**：`tests/contract_test.py`（新建）、`tests/doc_test.py`（新建）、`tests/graybox_test.py`（新建）、`tests/install_test.ps1`（新建）、`tests/vendor_verify.py`（新建）、`docs/BROWSER_COMPAT.md`（新建）。**禁改其他任何文件。**

**分块① API 契约形状测试**：新建 `tests/contract_test.py`（仅 Python 标准库，风格参照 integration.py）。对全部 16 个 REST 端点逐个发合法请求，断言：
- 每个响应顶层恰好三键 `code`(str) / `message`(str) / `data`(dict|null)
- `data` 内字段名与 docs/CONTRACT.md 一致（逐字段核对，不多不少）
- 字段类型正确：ID 为十进制字符串、时间为整数、enabled 为布尔
- 错误场景返回正确错误码字符串
- 覆盖全部 16 个端点 + 至少 8 种错误场景（400/401/403/404/409 各至少 1 个）
输出 `docs/evidence/contract/contract_results.json`，退出码 0=全通过。

**分块② 灰盒测试**：新建 `tests/graybox_test.py`。通过 API 触发操作，然后**直接打开 SQLite 数据库**验证数据落库正确性（白盒与黑盒之间——知道内部结构但通过外部接口驱动）：
- 注册→查 users 表验证 hash 前缀为 `$argon2id$`、role 为 USER
- 预约→查 reservations 表验证 status=CONFIRMED、source=DIRECT
- 取消→查 cancelled_at 非空、cancel_reason=USER
- 候补→查 waitlist 表 status=WAITING
- 取消触发补位→查 waitlist status=PROMOTED、reservations 新行 source=WAITLIST
- 操作事件→查 operation_events 表有对应 action 行
- 通知→查 notifications 表 kind 正确
每次检查后输出 PASS/FAIL，退出码 0=全通过。

**分块③ 文档一致性测试**：新建 `tests/doc_test.py`。
- 读取 docs/CONTRACT.md，提取全部端点路径列表
- 逐一检查每个端点在 src/http.c 的 dispatch 函数中有对应路由（grep 字符串匹配）
- 检查 README.md 中提及的文件（LICENSE、CHANGELOG、CONTRACT 等）实际存在
- 检查 docs/dependencies.lock.json 中每个组件的 vendor/ 目录确实存在
- 输出 PASS/FAIL 列表，退出码 0=全通过

**分块④ 安装测试**：新建 `tests/install_test.ps1`。模拟全新环境部署：
- 在临时目录复制整个项目（排除 build/data/artifacts）
- 运行 scripts/build.ps1 构建
- 运行 start-demo.ps1 初始化+启动
- 健康检查 + 注册+登录 API 验证
- 停止服务，清理临时目录
- 输出各步骤 PASS/FAIL

**分块⑤ 第三方组件完整性**：新建 `tests/vendor_verify.py`。
- 读取 docs/dependencies.lock.json 中每个组件的 SHA256
- 计算 vendor/ 下对应源码文件的实际 SHA256（对 amalgamation/单文件场景核对关键 .c 文件）
- 输出逐项 MATCH/MISMATCH 列表，退出码 0=全部匹配

**兼容性声明**：新建 `docs/BROWSER_COMPAT.md`。声明支持的浏览器（Chrome 90+、Edge 90+、Firefox 88+、Safari 14+）、不支持 IE、依赖的原生 API 列表（fetch/crypto.randomUUID/Intl.DateTimeFormat/CSS Grid/Flexbox）。

**验收与提交**：全部 5 个新建文件可独立运行且退出码 0 → `git add -A && git commit`（前缀 `r9: `）→ `git push -q origin r9/wb`。报告：各测试输出摘要、遗留问题。
