# opt/blueprints-r2 — 优化实施蓝图（规划参考集）

> 本分支为**纯规划分支**：不含任何 `src/` 代码改动，只产出实施级蓝图，供主项目后续迭代直接参考。
> 分支栈：`opt/blueprints-r2` ← `feat/opt-plan-r1`（对比分析+总计划+P0 安全头）← `32bfe0d`（v1.16.0）。
> 上游文档：[../../COMPETITIVE_ANALYSIS.md](../../COMPETITIVE_ANALYSIS.md) · [../../OPTIMIZATION_PLAN.md](../../OPTIMIZATION_PLAN.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [phase1-blueprint.md](phase1-blueprint.md) | Phase 1 三项实施蓝图：outbox 派发器 · OpenAPI 契约 · 通知实时化 |
| [phase2-blueprint.md](phase2-blueprint.md) | Phase 2 三项实施蓝图：QR 码签到 · iCal 订阅 · TOTP 二因素 |
| [references.md](references.md) | 已验证的 GitHub 参考实现索引（路径均经 2026-09-21 实证） |

## 每份蓝图的固定结构

**目标与前置 → 数据模型 → API 面 → 线程与事务模型 → CLI 开关 → 安全考量 → 测试矩阵（映射 17 通道）→ 验收清单 → 回滚与开关 → 工作量估计**

全部设计遵循主项目既有惯例：`BEGIN IMMEDIATE` 单写者事务、持久化回执幂等、审计动作入 `operation_events`、契约文档 + doc_test 一致性门禁、零第三方运行时依赖（vendor 源码入库 + SHA256 锁定）。

## 主项目采纳指引

1. **合并顺序建议**：先并行线的 service.c 模块化拆分（r43，`booking/asset/stats/token` 四域），再合 `feat/opt-plan-r1`（其 P0 安全头改动落在 `http.c` 的响应发送点，拆分不影响该处，但需在拆分后代码上重放验证），最后按本蓝图逐项立项。
2. **每项独立成轮**：沿用"一轮一交付+全通道回归"的迭代纪律；蓝图中的验收清单可直接转写为集成测试断言编号（T-Outbox-1…/T-QR-1…）。
3. **门禁联动**：任何新增端点必须同步 `docs/CONTRACT.md` 并通过 doc_test；新增 vendor 依赖必须进 `docs/dependencies.lock.json` + `tests/vendor_verify.py`。
4. **不做承诺**：蓝图中标记"评估后不做"的项（如 SMTP 直连、WebSocket v1）与 [OPTIMIZATION_PLAN.md](../../OPTIMIZATION_PLAN.md) 的"不做清单"口径一致，避免重复评估。
