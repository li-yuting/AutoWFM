# 看板截图"数据未加载完成"修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 截图前等待确定性就绪信号（所有图表动画完成）、Chart.js 本地化消灭 CDN 变量、截出疑似空白图时重试一次、仍坏则不发图只发 markdown（日志留痕）。

**Architecture:** 看板端在所有 Chart.js 图表动画完成时置 `body[data-ready]="1"` 并本地伺服 Chart.js；采集端 `take_screenshot` 用 `wait_for_function` 等该信号（超时退化为旧的固定 5 秒等待），截图前滚动预热光栅化，截图后用 Pillow 分水平条带检测空白（任一条带灰度级数 ≤4 判空白），两轮仍坏返回 None——`send_report` 现有逻辑自动只发 markdown。规格见 `docs/superpowers/specs/2026-09-04-screenshot-readiness-design.md`。

**Tech Stack:** Python 3（Playwright sync API、Pillow）、原生 JS（Chart.js v4）、Flask 内建 `/static/` 路由、纯 assert 测试（无 pytest）。

## Global Constraints

- Windows 环境；一律用 `.\.venv\Scripts\python.exe`，命令前设 `$env:PYTHONIOENCODING="utf-8"`。
- 测试是纯 `assert` 直跑（**无 pytest**）：`.\.venv\Scripts\python.exe tests\test_notify.py`；`tests/test_notify.py` 新增测试函数必须同时登记进文件底部 `main()` 的调用列表。
- CI 是 Ubuntu + Python 3.14 跑 `tests/test_*.py`：代码必须跨平台（用 `Path`/`os.path` 拼路径，勿写死 `\`）。
- **git 索引里有与本工作无关的预暂存删除（docs/superpowers 下 5 个旧文件）和未暂存的 `.gitignore`/`config.yaml` 改动。所有提交必须用 pathspec 限定：`git commit -m "..." -- <具体文件...>`；禁止 `git add -A`、`git add .`、不带 pathspec 的裸 `git commit`。**
- Conventional Commits + 中文描述（如 `feat(notify): ...`）。
- 不新增第三方依赖（Pillow、numpy、playwright 均已在 `requirements.txt`）。
- `collector/notify.py` 顶层**不得** import `dashboard` 或 `collector.scheduler`（AGENTS.md 约束）；playwright/PIL 保持函数内懒导入，与该文件现状一致。
- `data/screenshot.png` 已被 `.gitignore`（第 24 行）忽略，测试写它不会弄脏 git 树。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `collector/notify.py` | 修改 | 新增 `_looks_blank` / `_wait_ready` / `_warm_raster` + 两个常量；重写 `take_screenshot` 主体 |
| `dashboard/templates/dashboard.html` | 修改 | Chart.js 引用改本地静态文件；就绪信号 JS（`_pendingCharts`/`_chartDone`） |
| `dashboard/static/chart.umd.min.js` | 新增（下载） | 本地 Chart.js v4，消灭公网 CDN 依赖 |
| `tests/test_notify.py` | 修改 | 新增 `test_looks_blank` / `test_wait_ready` / `test_warm_raster` / `test_take_screenshot_retry` + main() 登记 |
| `tests/test_dashboard_static.py` | 新增 | 守卫：静态文件在、模板无 CDN 引用、模板可编译、就绪信号已接线 |

---

### Task 1: `_looks_blank` 空白检测（纯函数，无浏览器）

**Files:**
- Modify: `collector/notify.py`（在 `def take_screenshot(` 上方插入）
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: 无
- Produces: `_looks_blank(path, bands=6) -> bool`（True=疑似空白；Pillow 任何异常返回 False，fail-open）

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 的 `test_take_screenshot_failure` 函数（整块）之后追加：

```python
def test_looks_blank():
    from PIL import Image
    import numpy as np
    d = _tmp()
    solid = os.path.join(d, "solid.png")
    Image.new("RGB", (60, 60), (10, 15, 23)).save(solid)
    assert notify._looks_blank(solid) is True, "纯色图应判空白"
    noisy = os.path.join(d, "noisy.png")
    Image.fromarray(np.random.default_rng(7).integers(0, 256, (60, 60, 3), dtype=np.uint8)).save(noisy)
    assert notify._looks_blank(noisy) is False, "噪点图不应判空白"
    half = os.path.join(d, "half.png")
    im = Image.fromarray(np.random.default_rng(8).integers(0, 256, (60, 60, 3), dtype=np.uint8))
    im.paste((10, 15, 23), (0, 30, 60, 60))
    im.save(half)
    assert notify._looks_blank(half) is True, "下半纯色应判空白(条带逻辑)"
    bad = os.path.join(d, "bad.png")
    with open(bad, "wb") as f:
        f.write(b"not a png")
    assert notify._looks_blank(bad) is False, "坏文件 fail-open"
    print("looks_blank OK")
```

并在 `main()` 里登记（old→new 精确替换）：

```python
# old
    test_take_screenshot_failure()
    test_check_alerts_hotline()
# new
    test_take_screenshot_failure()
    test_looks_blank()
    test_check_alerts_hotline()
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：FAIL，`AttributeError: module 'collector.notify' has no attribute '_looks_blank'`（在 test_looks_blank 处中断）。

- [ ] **Step 3: 最小实现**

在 `collector/notify.py` 中，把（唯一出现的）`def take_screenshot(url, dash_token=None):` 一行前插入：

```python
def _looks_blank(path, bands=6):
    """截图是否疑似空白:灰度后按水平条带统计灰度级数,任一条带 <=4 级判空白。

    未渲染区域呈纯色(1 级);正常条带必含文字/图表抗锯齿灰阶(远超 4 级)。
    Pillow 任何异常 -> False(fail-open:检测不了就放行,宁漏勿误杀)。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("L")
        w, h = im.size
        if not w or not h:
            return False
        band_h = max(1, h // bands)
        for i in range(bands):
            top, bottom = i * band_h, min((i + 1) * band_h, h)
            if top >= bottom:
                continue
            colors = im.crop((0, top, w, bottom)).getcolors(maxcolors=4096)
            if colors is not None and len(colors) <= 4:
                return True
        return False
    except Exception as e:
        log.warning(f"[截图] 空白检测异常(fail-open): {e}")
        return False


```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：末行 `ALL notify tests OK`。

- [ ] **Step 5: 提交（pathspec 限定！）**

```powershell
git commit -m "feat(notify): 截图空白检测 _looks_blank(灰度级数判据,条带式)" -- collector/notify.py tests/test_notify.py
```

---

### Task 2: `_wait_ready` 就绪等待 + `_warm_raster` 光栅化预热

**Files:**
- Modify: `collector/notify.py`（插在 `_looks_blank` 与 `take_screenshot` 之间）
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: 无（`pg` 是 Playwright page 或测试假对象，鸭子类型）
- Produces:
  - 常量 `READY_TIMEOUT = 20`（秒）、`FALLBACK_WAIT_MS = 5000`
  - `_wait_ready(pg, timeout=READY_TIMEOUT) -> bool`：等 `body[data-ready]`；只捕获 Playwright `TimeoutError`（超时→内部做 `pg.wait_for_timeout(FALLBACK_WAIT_MS)` 兜底→返回 False）；其他异常向上抛
  - `_warm_raster(pg) -> None`：滚动到底→300ms→回顶→200ms；异常吞掉只记日志

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 的 `test_looks_blank` 函数之后追加：

```python
def test_wait_ready():
    from playwright.sync_api import TimeoutError as PwTimeout

    class _PgOk:
        def wait_for_function(self, expr, timeout=None):
            assert "dataset.ready" in expr, expr
            return True
    assert notify._wait_ready(_PgOk(), timeout=1) is True

    class _PgTimeout:
        def __init__(self):
            self.waited = []
        def wait_for_function(self, expr, timeout=None):
            raise PwTimeout("timeout")
        def wait_for_timeout(self, ms):
            self.waited.append(ms)
    pg = _PgTimeout()
    assert notify._wait_ready(pg, timeout=1) is False
    assert pg.waited == [notify.FALLBACK_WAIT_MS], "超时后应固定兜底等待"

    class _PgBoom:
        def wait_for_function(self, expr, timeout=None):
            raise RuntimeError("browser gone")
    try:
        notify._wait_ready(_PgBoom(), timeout=1)
        assert False, "非超时异常应向上抛"
    except RuntimeError:
        pass
    print("wait_ready OK")

def test_warm_raster():
    calls = []
    class _Pg:
        def evaluate(self, js):
            calls.append(js)
        def wait_for_timeout(self, ms):
            calls.append(ms)
    notify._warm_raster(_Pg())
    assert len(calls) == 4, calls          # scrollTo底 + 300ms + scrollTo顶 + 200ms
    assert "scrollTo" in calls[0] and "scrollTo" in calls[2]
    class _PgBad:
        def evaluate(self, js):
            raise RuntimeError("no dom")
        def wait_for_timeout(self, ms):
            pass
    notify._warm_raster(_PgBad())          # 异常应被吞掉,不抛
    print("warm_raster OK")
```

`main()` 登记（承接 Task 1 后的现状）：

```python
# old
    test_looks_blank()
    test_check_alerts_hotline()
# new
    test_looks_blank()
    test_wait_ready()
    test_warm_raster()
    test_check_alerts_hotline()
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：FAIL，`AttributeError: ... has no attribute '_wait_ready'`。

- [ ] **Step 3: 最小实现**

在 `collector/notify.py` 中，`def take_screenshot(url, dash_token=None):` 一行前（即 `_looks_blank` 之后）插入：

```python
# 截图就绪等待(秒)与超时后兜底固定等待(毫秒,即旧行为的 5 秒)
READY_TIMEOUT = 20
FALLBACK_WAIT_MS = 5000


def _wait_ready(pg, timeout=READY_TIMEOUT):
    """等待看板就绪信号 body[data-ready]=1(全部图表动画完成);超时退化为固定等待并返回 False。

    只捕获 Playwright TimeoutError(旧版看板无信号属预期降级);其他异常向上抛。"""
    try:
        from playwright.sync_api import TimeoutError as PwTimeout
        pg.wait_for_function("document.body && document.body.dataset.ready === '1'",
                             timeout=timeout * 1000)
        return True
    except PwTimeout:
        log.warning(f"[截图] 就绪信号超时({timeout}s),退化为固定等待 {FALLBACK_WAIT_MS}ms")
        pg.wait_for_timeout(FALLBACK_WAIT_MS)
        return False


def _warm_raster(pg):
    """滚动到底再回顶,预热长页面光栅化(防全页截图出现未渲染条带);失败仅记日志。"""
    try:
        pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        pg.wait_for_timeout(300)
        pg.evaluate("window.scrollTo(0, 0)")
        pg.wait_for_timeout(200)
    except Exception as e:
        log.warning(f"[截图] 光栅化预热失败(忽略): {e}")


```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：末行 `ALL notify tests OK`。

- [ ] **Step 5: 提交（pathspec 限定！）**

```powershell
git commit -m "feat(notify): 截图就绪等待 _wait_ready 与光栅化预热 _warm_raster" -- collector/notify.py tests/test_notify.py
```

---

### Task 3: 重写 `take_screenshot`（就绪信号 + 空白检测 + 重试 1 次）

**Files:**
- Modify: `collector/notify.py`（整体替换 `take_screenshot` 函数体）
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: Task 1 的 `_looks_blank(path, bands=6)`、Task 2 的 `_wait_ready(pg, timeout)` / `_warm_raster(pg)`
- Produces: 公开契约不变——`take_screenshot(url, dash_token=None) -> str | None`（成功返回 `data/screenshot.png` 路径；两轮均疑似空白或异常返回 None）

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 的 `test_warm_raster` 函数之后追加：

```python
def test_take_screenshot_retry():
    # 注意:本测试会写 data/screenshot.png(已 gitignore,且每次推送都会重新生成,可覆盖)
    from PIL import Image
    import numpy as np
    d = _tmp()
    solid = os.path.join(d, "solid.png")
    Image.new("RGB", (60, 60), (11, 15, 23)).save(solid)
    noisy = os.path.join(d, "noisy.png")
    Image.fromarray(np.random.default_rng(9).integers(0, 256, (60, 60, 3), dtype=np.uint8)).save(noisy)

    def _fake_page(shots):
        # shots: 每次 screenshot 依次拷入目标路径的源文件列表(模拟第1轮空白/第2轮正常)
        class _Pg:
            def __init__(self):
                self.goto_calls = 0
            def set_extra_http_headers(self, h): pass
            def goto(self, *a, **k): self.goto_calls += 1
            def wait_for_function(self, *a, **k): return True
            def wait_for_timeout(self, ms): pass
            def evaluate(self, js): pass
            def screenshot(self, path=None, full_page=False):
                shutil.copyfile(shots.pop(0), path)
        return _Pg()

    def _fake_sync_pw(pg):
        pw = MagicMock()
        pw.__enter__.return_value = pw
        pw.chromium.launch.return_value = MagicMock(new_page=lambda **k: pg, close=lambda: None)
        return pw

    # 第1轮截出空白图 -> 重载重试 -> 第2轮正常 -> 返回路径
    pg1 = _fake_page([solid, noisy])
    with patch("playwright.sync_api.sync_playwright", return_value=_fake_sync_pw(pg1)):
        res = notify.take_screenshot("http://x/")
    assert res is not None and pg1.goto_calls == 2, (res, pg1.goto_calls)
    assert Path("data/screenshot.png").exists()

    # 两轮均空白 -> 放弃,返回 None
    pg2 = _fake_page([solid, solid])
    with patch("playwright.sync_api.sync_playwright", return_value=_fake_sync_pw(pg2)):
        assert notify.take_screenshot("http://x/") is None
    assert pg2.goto_calls == 2
    print("take_screenshot_retry OK")
```

`main()` 登记（承接 Task 2 后的现状）：

```python
# old
    test_wait_ready()
    test_warm_raster()
    test_check_alerts_hotline()
# new
    test_wait_ready()
    test_warm_raster()
    test_take_screenshot_retry()
    test_check_alerts_hotline()
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：FAIL——`test_take_screenshot_retry` 里 `pg1.goto_calls == 2` 断言失败（旧实现只 goto 一次且无空白检测，第一轮 solid 就返回路径，goto_calls==1）。

- [ ] **Step 3: 整体替换 `take_screenshot`**

`collector/notify.py` 中把现有整个 `take_screenshot` 函数（从 `def take_screenshot(url, dash_token=None):` 到 `        return None`，即旧 227-249 行那段）替换为：

```python
def take_screenshot(url, dash_token=None):
    """Playwright 截图 -> data/screenshot.png;失败或两轮均疑似空白返回 None。

    每轮:goto(networkidle) -> 等就绪信号(超时退化固定 5s)-> 光栅化预热 -> 全页截图
    -> 空白检测;疑似空白重载重试 1 次,仍坏放弃(调用方只发 markdown,日志留痕)。
    dash_token 非空时带 Authorization: Bearer header(看板启用认证后必需)。"""
    try:
        from playwright.sync_api import sync_playwright
        Path("data").mkdir(exist_ok=True)
        path = str(Path("data") / "screenshot.png")
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_page(viewport={"width": 1920, "height": 1080})
                if dash_token:
                    pg.set_extra_http_headers({"Authorization": f"Bearer {dash_token}"})
                for attempt in (1, 2):
                    pg.goto(url, wait_until="networkidle", timeout=30000)
                    ready = _wait_ready(pg)
                    _warm_raster(pg)
                    pg.screenshot(path=path, full_page=True)
                    if not _looks_blank(path):
                        log.info(f"截图已保存: {path}(第{attempt}次,就绪信号={'已' if ready else '未'}确认)")
                        return path
                    log.warning(f"[截图] 第{attempt}次截图疑似空白,{'重载重试' if attempt == 1 else '放弃发送'}")
            finally:
                b.close()
    except Exception as e:
        log.error(f"截图失败: {e}")
    return None
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
预期：末行 `ALL notify tests OK`（含旧的 `test_take_screenshot_failure`：sync_playwright 抛异常→外层捕获→None，仍通过）。

- [ ] **Step 5: 提交（pathspec 限定！）**

```powershell
git commit -m "feat(notify): take_screenshot 改就绪信号等待+空白检测重试(修偶发半白截图)" -- collector/notify.py tests/test_notify.py
```

---

### Task 4: Chart.js 本地化（消灭公网 CDN 变量）

**Files:**
- Create: `dashboard/static/chart.umd.min.js`（下载）
- Modify: `dashboard/templates/dashboard.html:8`
- Test: `tests/test_dashboard_static.py`（新建）

**Interfaces:**
- Consumes: Flask 内建 `/static/<filename>` 路由（无需任何配置）
- Produces: `dashboard/static/chart.umd.min.js`（Chart.js v4 umd 压缩版，仓库内二进制资产）；模板 `<script>` 指向 `{{ url_for('static', filename='chart.umd.min.js') }}`

- [ ] **Step 1: 下载 Chart.js 入库**

```powershell
New-Item -ItemType Directory -Force dashboard\static | Out-Null
Invoke-WebRequest -Uri "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js" -OutFile dashboard\static\chart.umd.min.js
(Get-Item dashboard\static\chart.umd.min.js).Length
```
预期：Length > 100000（v4 umd 压缩版约 200KB）。若 jsdelivr 被墙，改用 `https://unpkg.com/chart.js@4/dist/chart.umd.min.js` 重试。

- [ ] **Step 2: 改模板 script 标签**

`dashboard/templates/dashboard.html` 第 8 行（唯一出现的 CDN 引用）：

```html
<!-- old -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<!-- new -->
<script src="{{ url_for('static', filename='chart.umd.min.js') }}"></script>
```

- [ ] **Step 3: 写守卫测试（新文件 `tests/test_dashboard_static.py`）**

```python
# -*- coding: utf-8 -*-
"""看板静态资源守卫:Chart.js 必须本地伺服,不得回退公网 CDN;模板必须可编译。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(ROOT, "dashboard", "templates", "dashboard.html")
_JS = os.path.join(ROOT, "dashboard", "static", "chart.umd.min.js")

def test_chartjs_vendored():
    assert os.path.isfile(_JS), f"缺少本地 Chart.js: {_JS}"
    with open(_JS, "rb") as f:
        data = f.read()
    assert len(data) > 100_000, f"chart.umd.min.js 疑似不完整: {len(data)} 字节"
    assert b"Chart" in data, "chart.umd.min.js 内容异常"

def test_template_no_cdn():
    with open(_TPL, encoding="utf-8") as f:
        src = f.read()
    assert "cdn.jsdelivr.net" not in src, "模板不得引用公网 CDN"
    assert "chart.umd.min.js" in src, "模板应引用本地 Chart.js"

def test_template_compiles():
    from dashboard.app import app
    app.jinja_env.get_template("dashboard.html")   # 编译(不渲染),捕获 Jinja 语法错误

def main():
    test_chartjs_vendored()
    test_template_no_cdn()
    test_template_compiles()
    print("ALL dashboard static tests OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_static.py
```
预期：`ALL dashboard static tests OK`。

- [ ] **Step 5: 提交（含两个新文件，先精确 add 再 pathspec 提交；禁止 add -A！）**

```powershell
git add dashboard/static/chart.umd.min.js tests/test_dashboard_static.py
git commit -m "feat(dashboard): Chart.js 本地化,去除公网 CDN 依赖" -- dashboard/static/chart.umd.min.js tests/test_dashboard_static.py dashboard/templates/dashboard.html
```

（说明：`git commit -- <path>` 不包含未跟踪文件，故新文件需先 `git add`；pathspec 提交不会带上索引里那 5 个无关的预暂存删除。）

---

### Task 5: 看板就绪信号 JS（图表动画完成 → `body[data-ready]`）

**Files:**
- Modify: `dashboard/templates/dashboard.html`（`buildChart` / `renderAll` / 新增计数器）
- Test: `tests/test_dashboard_static.py`

**Interfaces:**
- Consumes: Chart.js v4 `options.animation.onComplete` 回调（Task 4 的本地文件）
- Produces: 页面级约定 `document.body.dataset.ready === "1"` ⇔ 所有图表动画完成（Task 2 `_wait_ready` 等的就是它）

- [ ] **Step 1: 写失败测试**

在 `tests/test_dashboard_static.py` 的 `test_template_compiles` 函数之后追加：

```python
def test_ready_signal_wired():
    with open(_TPL, encoding="utf-8") as f:
        src = f.read()
    assert "_pendingCharts" in src and "_chartDone" in src, "就绪信号计数器缺失"
    assert "onComplete:_chartDone" in src, "图表动画完成回调未接线"
    assert 'dataset.ready = "1"' in src, "body[data-ready] 置位逻辑缺失"
```

并把 `main()` 改为：

```python
def main():
    test_chartjs_vendored()
    test_template_no_cdn()
    test_template_compiles()
    test_ready_signal_wired()
    print("ALL dashboard static tests OK")
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_static.py
```
预期：FAIL，`AssertionError: 就绪信号计数器缺失`。

- [ ] **Step 3: 改模板 JS（三处精确替换）**

`dashboard/templates/dashboard.html`：

**3a.** 在 `var CHARTS = [];` 之前插入计数器（old→new）：

```js
// old
var CHARTS = [];
function renderAll(){
// new
/* ===== 截图就绪信号:所有图表动画完成 -> body[data-ready]=1(collector 截图据此等待) ===== */
var _pendingCharts = 0;
function _chartDone(){
  if(--_pendingCharts <= 0){ _pendingCharts = 0; document.body.dataset.ready = "1"; }
}

var CHARTS = [];
function renderAll(){
```

**3b.** `buildChart` 尾部（计数 + 动画回调）：

```js
// old
  var el = document.getElementById(canvasId);
  if(!el) return null;
  return new Chart(el.getContext('2d'), {
    type:'bar',
    data:{ labels:a.labels, datasets:datasets },
    options:{
      responsive:true, maintainAspectRatio:false,
      interaction:{ mode:'index', intersect:false },
      plugins:{ legend:c.legend, tooltip:{ mode:'index', intersect:false } },
      scales:scales
    }
  });
// new
  var el = document.getElementById(canvasId);
  if(!el) return null;
  _pendingCharts++;
  return new Chart(el.getContext('2d'), {
    type:'bar',
    data:{ labels:a.labels, datasets:datasets },
    options:{
      responsive:true, maintainAspectRatio:false,
      animation:{ onComplete:_chartDone },
      interaction:{ mode:'index', intersect:false },
      plugins:{ legend:c.legend, tooltip:{ mode:'index', intersect:false } },
      scales:scales
    }
  });
```

**3c.** `renderAll` 本体（重置计数 + 无图表直接就绪）：

```js
// old
  CHARTS.forEach(function(ch){ if(ch) ch.destroy(); });
  CHARTS = [];
  Object.keys(INBOUND).forEach(function(name){
    CHARTS.push(buildChart('chart_in_' + name, name, INBOUND[name], 'in'));
  });
  Object.keys(OUTBOUND).forEach(function(name){
    CHARTS.push(buildChart('chart_out_' + name, name, OUTBOUND[name], 'out'));
  });
}
// new
  CHARTS.forEach(function(ch){ if(ch) ch.destroy(); });
  CHARTS = [];
  _pendingCharts = 0;
  Object.keys(INBOUND).forEach(function(name){
    CHARTS.push(buildChart('chart_in_' + name, name, INBOUND[name], 'in'));
  });
  Object.keys(OUTBOUND).forEach(function(name){
    CHARTS.push(buildChart('chart_out_' + name, name, OUTBOUND[name], 'out'));
  });
  if(_pendingCharts === 0) document.body.dataset.ready = "1";   /* 无图表时直接就绪 */
}
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_static.py
```
预期：`ALL dashboard static tests OK`。

- [ ] **Step 5: 提交（pathspec 限定！）**

```powershell
git commit -m "feat(dashboard): 图表动画完成就绪信号 body[data-ready]" -- dashboard/templates/dashboard.html tests/test_dashboard_static.py
```

---

### Task 6: 全量回归 + 实机验证 + 部署提示

**Files:**
- 无代码改动（只验证）

**Interfaces:**
- Consumes: 全部前序任务成果
- Produces: 验证记录；无新接口

- [ ] **Step 1: 跑全部测试**

```powershell
$env:PYTHONIOENCODING="utf-8"; Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```
预期：每个文件各自打印 `ALL ... OK`，无 traceback、无 `[exit code: N]`。

- [ ] **Step 2: 实机验证（需要本机已 `playwright install chromium`；无数据也能验，页面会显示"无数据"但结构完整）**

终端 1 启看板（**若本机 8080 已被 manager 拉起的看板占用，先按 Step 3 重启 manager，再用现有 8080 验证，不要重复起实例**）：

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m dashboard.app
```

终端 2 截一张并计时：

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "from collector import notify; import time; t=time.time(); p=notify.take_screenshot('http://127.0.0.1:8080/'); print('saved:', p, 'in', round(time.time()-t,1), 's')"
```

预期：`saved: data\screenshot.png in <30 s`；用图像查看器（或视觉工具）确认截图完整（标题/卡片/图表俱全，非空白/半白）。若本机 `.env` 配置了 `AUTOWFM_DASH_TOKEN`，看板会 401——此验证假设本地开发态（token 为空）；生产链路由 `send_report` 从 `config.yaml` 的 `notify.dash_token` 传 token，不受影响。

- [ ] **Step 3: 部署提示（写给使用者，不是代码步骤）**

改动要重启才生效：退出并重启 `manager.py`（注意其单实例锁，确认旧实例完全退出后再启动），由它拉起的采集器与看板进程随之更新。首个推送周期观察日志：正常应出现 `截图已保存: ... (第1次,就绪信号=已确认)`；若出现 `就绪信号超时` 说明看板端还是旧代码（检查是否真的重启了）。

---

## Self-Review 记录

- 规格覆盖：本地 Chart.js（Task 4）、就绪信号（Task 5）、`_wait_ready`/`_warm_raster`/`_looks_blank`（Task 1-2）、重试循环与静默跳过（Task 3）、守卫测试（Task 4-5）、兼容/部署（Task 6 + 各降级路径）——全覆盖。
- 占位符扫描：无 TBD/TODO；所有代码步骤含完整代码。
- 类型一致性：`_looks_blank(path, bands=6)`、`_wait_ready(pg, timeout=READY_TIMEOUT)`、`_warm_raster(pg)`、常量 `READY_TIMEOUT`/`FALLBACK_WAIT_MS`、JS 侧 `_pendingCharts`/`_chartDone`/`dataset.ready`——各任务间名称与签名一致。
