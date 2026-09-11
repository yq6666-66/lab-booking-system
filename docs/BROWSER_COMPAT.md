# 浏览器兼容性声明

本文件声明实验室预约系统前端（`web/` 下的静态页面）所支持的浏览器范围、不支持的浏览器，以及支撑该结论的原生 API 清单。结论由**代码实际使用的语言特性与原生接口**推导，并标注了实测范围与未实测范围。

## 一、支持范围

| 浏览器 | 目标最低版本 | 结论 |
| --- | --- | --- |
| Google Chrome | 90+ | 支持 |
| Microsoft Edge | 90+ | 支持（Chromium 内核，与 Chrome 同源） |
| Mozilla Firefox | 88+ | 支持（见第三节关于版本下界的说明） |
| Apple Safari | 14+ | 支持（见第三节关于版本下界的说明） |
| Internet Explorer | 任意版本 | **不支持** |

目标最低版本以浏览器**发布年份的长期支持线**为准；下文第三节给出由依赖能力推导出的实际下界。

## 二、不支持的浏览器

**Internet Explorer 11 及更早版本不受支持**，原因是本系统依赖的能力在 IE 中缺失或不可靠：

- `fetch` 与 `Promise`：IE 完全没有，前端所有接口调用建立在二者之上；
- `async/await`：IE 不支持该语法；
- `crypto.randomUUID`：IE 无 `crypto` 对象；
- CSS Grid 与 CSS 自定义属性（`var()`）：IE 仅部分支持旧版 Grid，且不支持自定义属性；
- `Element.replaceChildren`、`Intl.DateTimeFormat` 的 `timeZone` 选项：IE 缺失或不完整。

## 三、依赖的原生能力

| 能力 | 用途 | 最早可用版本（Chrome / Firefox / Safari） |
| --- | --- | --- |
| `fetch` + `Promise` + `async/await` | 所有 REST 调用与异步流程 | 42 / 39 / 10.1 |
| `Intl.DateTimeFormat` 带 `timeZone: 'Asia/Shanghai'` | 全站时间按北京时间展示 | 24 / 29 / 10 |
| `crypto.randomUUID()` | 生成写操作的 `request_id`（幂等去重的关键） | **92** / **95** / **15.4** |
| `sessionStorage` | 暂存未确认操作以便原编号重试 | 5 / 2 / 4 |
| `URL.createObjectURL` + `Blob` | 管理员导出统计 CSV 时触发下载 | 8 / 4 / 6 |
| `Element.replaceChildren()` | 列表渲染与清空 | 86 / 78 / 14 |
| 可选链 `?.` 与空值合并 `??` | 前端数据容错取值 | 80 / 74 / 13.1 |
| CSS Grid、Flexbox、`@media` 媒体查询、CSS 自定义属性 | 页面布局与响应式 | 57 / 52 / 10.1 |

## 四、版本下界的实际决定因素

上表中 `crypto.randomUUID()` 的要求（Chrome 92 / Firefox 95 / Safari 15.4）**高于**第一节声明的目标线（Firefox 88、Safari 14）。也就是说：

- 在 **Chrome 90 / Edge 90** 上，`crypto.randomUUID` 尚不可用（该 API 自 Chrome 92 起提供）；
- 在 **Firefox 88–94**、**Safari 14–15.3** 上同样不可用。

因此若要严格保证"所有写操作可提交"，实际下界应取 **Chrome 92+ / Edge 92+ / Firefox 95+ / Safari 15.4+**。第一节列出的 Chrome 90+ / Edge 90+ / Firefox 88+ / Safari 14+ 是**页面可加载、可浏览与读取**的下界；落在该区间内的浏览器在提交写操作（预约、候补、改密等）时会因 `crypto.randomUUID` 缺失而失败。

> 该差异属**已知限制**，本轮未改动前端代码（不在本任务的文件白名单内），仅在此如实记录，供后续决定"提升声明下限"或"为老浏览器增加 `crypto.getRandomValues` 降级实现"时参考。

## 五、运行前提

- **必须是安全上下文（secure context）**：`crypto.randomUUID()` 仅在 HTTPS 或 `localhost` / `127.0.0.1` 下可用。本系统设计为仅监听回环地址（`127.0.0.1`），天然满足该条件；若将来部署到远程主机，必须启用 HTTPS，否则 `crypto.randomUUID` 不可用、写操作无法提交。
- **必须允许会话 Cookie**：登录依赖 `Set-Cookie`（`HttpOnly; SameSite=Strict`），浏览器需允许第一方 Cookie。
- **需允许内联样式表与脚本的普通加载**：前端不使用内联脚本，故无需放宽 CSP；页面仅引用同源的 `/app.js` 与 `/style.css`。
- **JavaScript 必须启用**：页面为纯前端渲染，禁用 JS 将只剩空壳。

## 六、验证状态（如实说明）

| 项目 | 状态 |
| --- | --- |
| Chromium 1228（Playwright 无头模式） | **已实测**：登录入口切换、预约/签到、通知、会话管理、分页、CSV 导出、管理端统计与运行指标均通过；证据见 `docs/evidence/ui-r3/`、`docs/evidence/ui-r5/` |
| Chrome / Edge 桌面版（真机） | **未实测**（内核与上述 Chromium 同源，风险低） |
| Firefox 88+ / 95+ | **未实测**，结论基于 API 可用版本的公开资料推导 |
| Safari 14+ / 15.4+ | **未实测**，同上 |
| IE 11 | **未实测**，依据其缺失的能力判定为不支持 |
| 移动端浏览器 | **未实测**；页面含 `@media (max-width: 600px)` 响应式规则，窄屏布局按 CSS 规则生效，但未在移动设备上人工确认 |

自动化浏览器测试只覆盖了 Chromium 内核。若要对外宣称跨浏览器支持，需要在真实 Firefox / Safari 上补做人工走查，本轮未执行。
