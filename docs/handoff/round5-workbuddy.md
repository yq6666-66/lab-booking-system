# WorkBuddy 任务书（第五轮：CI 门禁 + 基准实验 + 性能指标）

> 使用方法：整段"提示词"复制给 WorkBuddy 即可开工。基线 feat/c-booking@116ef12。

---

## 提示词（原样复制）

接手 C 语言实验室预约系统第五轮升级中的三个分块：CI 门禁、基准实验脚本、性能指标。项目在 `C:\Users\admin\Desktop\毕业设计`（git 仓库，当前分支 feat/c-booking，你要基于它的最新提交工作）。

**第 0 步 环境**：在仓库根执行 `git worktree add C:\Users\admin\Desktop\r5-wt-wb -b r5/wb feat/c-booking`，后续全部工作只在 `C:\Users\admin\Desktop\r5-wt-wb` 进行。禁止推 main、禁止改 feat/c-booking、禁止 rebase。同一文件多处修改必须串行编辑（并行编辑会相互覆盖）。

**文件白名单**（只允许改这些）：`.github/**`（新建）、`README.md`（仅徽章行）、`tests/benchmark.py`（新建）、`src/metrics.c`（新建）、`src/app.h`（仅 #endif 前追加声明块）、`src/http.c`（仅钩子与新路由）、`scripts/build.ps1`（仅在 $own 数组追加 `'src/metrics.c'` 一处）、`web/*`（仅指标区块）、`tests/integration.py`（仅末尾追加 T25）、`docs/CONTRACT.md`（仅追加 metrics 小节）。**禁改 src/db.c、src/service.c、src/main.c、tests/unit.c、docs/ 下其他文件。**

**分块① CI**：新建 `.github/workflows/ci.yml`。要点：push（main/feat/**）与 pull_request 触发；`windows-latest`；步骤 = checkout → 检测 gcc（不可用则 `choco install mingw -y --no-progress`）→ `powershell -File scripts/build.ps1`（该脚本自带 16 个单元测试门禁）→ 设 `$env:LAB_TEST_PASSWORD='Ci-Only-Password-77'` 后 `python tests/integration.py --quick --output tests/results-ci`（vendor 已入库，无需下载依赖）；第二个 job 跑 `scripts/build.ps1 -Analyze` 作静态分析门禁，失败上传测试结果产物。README.md 顶部标题下加一行徽章：`[![CI](https://github.com/yq6666-66/lab-booking-system/actions/workflows/ci.yml/badge.svg)](https://github.com/yq6666-66/lab-booking-system/actions/workflows/ci.yml)`。

**分块② 基准脚本**：新建 `tests/benchmark.py`（仅 Python 标准库，风格参照 tests/integration.py 的 Server/Client 封装）。两个实验：E1 读性能（1/5/10/20 客户端 × 固定请求数，GET /api/slots 与 /api/me/records 混合，输出吞吐与 p50/p95/p99）；E2 写争用（同场次 N 写者 vs 异场次 N 写者对比，输出吞吐与 503 计数）。参数 `--exe --clients --requests --output`，服务器用临时库与随机端口，口令走 `LAB_TEST_PASSWORD` 环境变量，不落盘口令。结果写 `docs/evidence/benchmark/benchmark.csv` 与 `summary.json`。

**分块③ 性能指标**：新建 `src/metrics.c`（声明追加到 `src/app.h` 的 `#endif` 之前，带 `/* r5/metrics */` 注释标记）。设计：`InterlockedIncrement64` 无锁计数器 + 延迟直方图（桶边界 ms：1/2/5/10/20/50/100/200/500/1000/2000 及溢出）。导出：`metrics_record_request(int status,double elapsed_ms)`、`metrics_inc_login()`、`metrics_init(void)`、`metrics_snapshot(void)`（返回 cJSON：`counters{requests_total,ok_2xx,err_4xx,err_5xx,db_busy_503,logins}` 与 `latency_ms{count,sum,max,buckets}`）。钩子只允许两处：`src/http.c` 的 `api()` 入口取 `QueryPerformanceCounter` 起点、send 标签处调用 `metrics_record_request(r.status,ms)`；`login()` 成功路径调 `metrics_inc_login()`。新增路由 `GET /api/admin/metrics`（仅管理员，仿照现有 /api/admin/stats 的写法）。serve() 里不需要显式 metrics_init（静态零初始化即可）。

**前端**：管理页新增"运行指标"区块（计数表 + 延迟桶表 + 刷新按钮），风格与现有"预约统计"区块一致，改动仅限 web/ 内。

**测试**：tests/integration.py 的 `regression()` 末尾追加 T25（注册行带 `# -- r5/metrics` 注释）：管理员读 /api/admin/metrics 返回 200 且 requests_total 随请求递增；普通用户 403；匿名 401。不要动其他测试。

**文档**：docs/CONTRACT.md 末尾追加"## 运行指标"小节：端点形状、计数口径、直方图桶边界、计数器为进程内存态重启清零。

**验收与提交**（全部必须通过才算完成）：依次跑 `powershell -File scripts/build.ps1`（16 单元测试必须全过）、设 LAB_TEST_PASSWORD 后 `python tests/integration.py --quick --output tests/results-wb`（必须全过）、`python tests/benchmark.py --output docs/evidence/benchmark` 跑通。代码风格对齐现有源码（紧凑单行函数、中文用户消息、错误码大写下划线）。完成后 `git add -A && git commit`（信息前缀 `r5: `）并 `git push -q origin r5/wb`，最后报告：改动文件清单、各验证命令输出结论、遗留问题。
