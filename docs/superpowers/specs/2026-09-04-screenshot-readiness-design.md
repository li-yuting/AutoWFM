# 看板截图"数据未加载完成"修复设计

- 日期：2026-09-04
- 状态：已与用户确认（方案 A）
- 影响范围：`dashboard/templates/dashboard.html`、`dashboard/static/`（新增）、`collector/notify.py`、`tests/`

## 背景与问题

`collector/notify.py` 的 `take_screenshot` 定时（:00/:15/:30/:45）对看板 `http://127.0.0.1:8080/` 全页截图并发企微。当前等待策略是 `goto(wait_until="networkidle")` + **固定等 5 秒**，无任何"渲染完成"确认，也无截图质量兜底。

偶发（少量）坏截图表现为**整页空白/半白**。链路分析出的三个根因：

1. **Chart.js 走 jsdelivr CDN**（`dashboard.html` 头部，render-blocking script）：国内访问间歇性慢/失败，body 渲染被公网阻塞，5 秒缓冲可能被吃光。
2. **固定 5 秒是猜的**：图表动画、长页面（全页截图数千像素高）光栅化在 CPU 抢占下（推送时刻撞上采集周期，同机还跑着 API/看板/shift/manager）可能超过 5 秒。
3. **无兜底**：即使截出空白图也照发不误。

用户确认：坏截图是"整页空白/半白"（非"数据旧一拍"，故不涉及 `_wait_cycle` 推送时序）；期望重试策略为**重试 1 次，仍失败则静默跳过图片、只发 markdown，日志留痕**。

## 目标

- 截图前有确定性的"页面已完整渲染"信号，不再依赖固定等待。
- 消灭 CDN 这个不可控变量。
- 坏图发出前能被检测拦住，重试一次，仍坏则不发图（其余推送不受影响）。

## 方案（已选 A：确定性就绪信号 + 本地 Chart.js + 空白检测兜底）

### 1. 看板端

**本地化 Chart.js**：下载与现 CDN 同版本线的 Chart.js v4 `chart.umd.min.js` 入库 `dashboard/static/chart.umd.min.js`；模板改用 `{{ url_for('static', filename='chart.umd.min.js') }}`。Flask 自动伺服 `/static/`。

**就绪信号**（内联 JS，约 10 行）：

```js
var _pendingCharts = 0;
function _chartDone(){ if(--_pendingCharts <= 0) document.body.dataset.ready = "1"; }
```

- `renderAll()` 按实际将构建的图表数设置 `_pendingCharts`；为 0 时直接置 ready（无数据日不死等）。
- `buildChart()` 的 Chart 配置加 `animation: { onComplete: _chartDone }`。
- 主题切换重建图表时 `_chartDone` 幂等，无影响。
- 语义：`body[data-ready]="1"` ⇔ 所有图表动画完成、页面完整呈现。

### 2. 采集端（`collector/notify.py`）

`take_screenshot(url, dash_token=None)` 签名不变（调用方零改动），内部拆三个模块级函数 + 重试循环：

- **`_wait_ready(pg, timeout=20)`**：`pg.wait_for_function("document.body && document.body.dataset.ready === '1'", timeout=timeout*1000)`；只捕获 Playwright `TimeoutError`（其他异常放行给外层），超时返回 False 并退回固定 5 秒等待（兼容"采集端已升级、看板未升级"的部署窗口）。
- **`_warm_raster(pg)`**：滚动到底 → 300ms → 回顶 → 200ms，预热长页面光栅化，防"半白"条带。
- **`_looks_blank(path, bands=6)`**：Pillow 灰度化后按水平条带统计灰度级数，任一条带 ≤4 个灰度级判空白。未渲染区域呈纯色（1 个级）；正常条带必含文字/图表的抗锯齿灰阶（远超 4 级）。不用主色占比阈值：仅含标题的条带非主色像素可低于 0.5%，会误杀好图。Pillow 出错返回 False（fail-open：检测不了就放行）。

主流程：

```
尝试 1..2 次：
    goto(url, wait_until="networkidle", timeout=30s)
    _wait_ready(pg)        # 超时 → 固定 5s 兜底
    _warm_raster(pg)
    全页截图 → data/screenshot.png
    非 _looks_blank(path) → 返回 path
    否则 log.warning，下一轮重新 goto
两轮均失败 → log.error，返回 None
```

`take_screenshot` 返回 None 时，`send_report` 现有逻辑自动只发 markdown、跳过图片。

### 3. 错误处理与兼容

| 场景 | 行为 |
|---|---|
| ready 信号超时（旧版看板/极端慢） | 退化为固定 5s 等待，继续截图，空白检测兜底 |
| 空白检测自身异常 | fail-open（返回"非空白"） |
| 两轮都空白 / 浏览器异常 | 返回 None，只发 markdown，日志 warning/error |
| 部署顺序 | 看板/采集端任意先后升级均可用（信号是可选增强） |
| 最坏耗时 | ~1 分钟内（goto 30s + 信号 20s + 兜底 5s + 截图），push_job 独立线程，markdown 先发不受影响 |

### 4. 测试

沿用纯 `assert` 惯例（CI 无浏览器）：

- `test_looks_blank`：Pillow 造纯色图（True）、噪点图（False）、上彩下纯色图（True，验证条带）、坏文件（False，fail-open）。
- `test_wait_ready`：假 page 对象——正常返回（True）、抛 Playwright `TimeoutError`（False 且兜底等待被调用）。
- `test_take_screenshot_retry`：mock `playwright.sync_api.sync_playwright`，第一轮产纯色图、第二轮产噪点图 → 断言 goto 调两次、返回路径；两轮均坏 → None。
- 新增 `tests/test_dashboard_static.py`：静态文件存在 + 模板不再引用 `cdn.jsdelivr.net`。
- 现有 `tests/test_notify.py` 全部保持通过（含 `test_take_screenshot_failure` 的 mock 方式）。

## 明确不做（YAGNI）

- 不加 config 开关（超时/重试用模块常量，测试经函数参数/常量注入覆盖）。
- 不动 `_wait_cycle`/推送时序（问题不在数据新鲜度）。
- 不做多轮重试/指数退避。
- 不动 shift/peakflow 等其他子项目页面。
