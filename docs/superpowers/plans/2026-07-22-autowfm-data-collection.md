# AutoWFM 数据采集与统计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 长驻进程,9-21 窗口每 5 分钟采集 7 路 WS 监控指标 + 2 路 requests 明细计数,分存 9 个独立 SQLite 库。

**Architecture:** APScheduler 驱动两个独立 5 分钟任务(WS 池7 / requests 池2),各自窗口 guard、`max_instances=1`。WS 每周期连一次取首帧提取指标;requests 下载当日 Excel(header=2)按组计数。9 个 SQLite 各一张表,时间序列。无告警/报表/截图实现(仅 `notify.py` 入口 stub)。

**Tech Stack:** Python 3.14、APScheduler、websocket-client、requests、pandas、openpyxl、xlrd、PyYAML、sqlite3(stdlib)、zoneinfo(stdlib)。

## Global Constraints

- venv:`D:\PythonProject\AutoWFM\.venv`(已装 websocket-client/requests/PyYAML;Task 1 补装 APScheduler/pandas/openpyxl/xlrd)。
- 测试用纯 `assert`(无 pytest),放 `tests/`,用 `python tests/test_x.py` 运行。
- 复用 v14 的 `_extract_*` 逻辑(`D:\PythonProject\hfqwfm\everyday\hfq_spider_v14.py`),扩展 statics 字段。
- 窗口 (9:00, 21:00]:首拍 09:05,末拍 21:00,144 拍/天。guard:`540 < mins <= 1260`。
- 9 个 SQLite:`data/<源名>.db`,每库一张表 `t`,`时间 TEXT` + 指标列 `INTEGER`。
- WS 无需认证;requests 中文表头;渠道来源值真的是"电话呼入呼入"/"在线客服呼入呼入"。
- 不存 raw、不存 ivrCount/hourHcCount/小时数组/SEAT 坐席明细/IM 坐席明细。

---

## File Structure

- `config.yaml` — 端点、调度、subs(7)、detail_modes(2)、secrets、storage、logging。
- `storage.py` — 9 库建表+插入(按源名分库)。
- `ws.py` — WS 每周期采集 `collect_one` + 指标提取器(复用 v14)。
- `detail.py` — requests 下载+Excel 解析+`count_groups`。
- `notify.py` — `send_alert`/`take_screenshot` stub。
- `scheduler.py` — APScheduler 两任务 + 窗口 guard + 线程池。
- `main.py` — 入口:加载配置、日志、启动调度器。
- `tests/test_storage.py` / `test_ws.py` / `test_detail.py` / `test_guard.py` / `smoke.py`。

---

### Task 1: Project setup

**Files:**
- Create: `config.yaml`, `.gitignore`
- Modify: venv deps

- [ ] **Step 1: 补装依赖**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe -m pip install APScheduler pandas openpyxl xlrd`
Expected: 4 包安装成功。

- [ ] **Step 2: git init + 目录**

```bash
cd D:\PythonProject\AutoWFM
git init
mkdir data logs tests
```
Expected: git 仓库初始化,data/logs/tests 目录存在。

- [ ] **Step 3: 写 `.gitignore`**

```gitignore
.venv/
__pycache__/
data/
logs/
*.pyc
```

- [ ] **Step 4: 写 `config.yaml`**

```yaml
endpoints:
  online: "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
  other:  "ws://monitor-datawarehouse-cloud.weicai.com.cn:7000/customer/monitor"
schedule:
  interval_minutes: 5
  window_start: "09:00"
  window_end: "21:00"
  timezone: "Asia/Shanghai"
ws:
  connect_timeout: 12
  recv_timeout: 8
  retry: 1
detail:
  timeout: 60
secrets:
  token: "USER_TOKEN_KEYp4KSLn1fKIrnPeBoLQkFcDo0jPyc80hYSnnYh9kqbae82e7a1ebace52f60566de65ed768c"
  tenementId: "201804131002426760327BiIC"
storage:
  dir: "data"
logging:
  path: "logs/autowfm.log"

seat_data: &seat_data
  skillCode: ""
  pickUpRankDeptId: ""
  busyRankDeptId: ""
  afterOverTimeDeptId: ""
  afterOverTimeStatiscsDeptId: ""
  agentStatusDeptId: ""

subs:
  - {name: "热线",      endpoint: other,  screen: STATICS,    data: {skillCode: "", numberType: HFQ_OFFICIAL}}
  - {name: "12378",     endpoint: other,  screen: STATICS,    data: {skillCode: "", numberType: SERVICE_12378}}
  - {name: "热线明细",   endpoint: other,  screen: SEAT,       skill: "252", data: {<<: *seat_data, skillCode: "252"}}
  - {name: "常规",      endpoint: other,  screen: SEAT,       skill: "520", data: {<<: *seat_data, skillCode: "520"}}
  - {name: "贷后",      endpoint: other,  screen: SEAT,       skill: "958", data: {<<: *seat_data, skillCode: "958"}}
  - {name: "12378明细",  endpoint: other,  screen: SEAT,       skill: "847", data: {<<: *seat_data, skillCode: "847", agentStatusDeptId: "q40YvMUfzHi1y3aOr89s8lx3mTk55OluQVhDCmYH"}}
  - {name: "在线",      endpoint: online, screen: IM_MONITOR, data: {skillCode: "", agentStatus: ""}}

detail_modes:
  会话记录:
    url: "https://callcenter-crm.weicai.com.cn/api/sheet/callLog/exportCL"
    date_fields: {start: staStartDt, end: endStartDt}
    date_format: "%Y-%m-%d %H:%M:%S"
    filter:
      channel_column: "渠道来源"
      channels: ["电话呼入呼入", "在线客服呼入呼入"]
      group_column: "处理组别"
      groups: ["转接一组", "转接二组", "贷后转接组"]
    data:
      token: ""
      tenementId: ""
      columns: "startDt"
      orders: "DESC"
      code: ""
      busiTypeId: ""
      cusId: ""
      cusName: ""
      cusNum: ""
      cusCard: ""
      ccNum: ""
      staStartDt: ""
      endStartDt: ""
      staEndDt: ""
      endEndDt: ""
      ivr: ""
      risk: ""
      crtId: ""
      fromTypeId: "TEL,ZXKF,QT"
      fromWayId: ""
      content: ""
      serviceTypeId: ""
      itemCode: ""
      threeTypeId: ""
      remark: ""
      staCode: ""
      callDuration: ""
      isSheet: ""
      startCrtDt: ""
      endCrtDt: ""
      startUpdDt: ""
      endUpdDt: ""
      endReasonCode: ""
      fundCode: ""
      dnis: ""
      serviceLabelCode: ""
      isDesensitization: true
      exports: "cusCode,cusName,serviceTypeName,itemName,threeTypeName,startDt,remark,cusNum,crtName,crtDeptName,serviceLabelNameList,blackFlagName,busiTypeName,startTime,endTime,callDuration,fromTypeName,disconnectName,isSheet,code,dnis,id,endReasonName,staName,complaintName,appeaseName"
  工单明细:
    url: "https://callcenter-crm.weicai.com.cn/api/sheet/list/exportCLSheet"
    date_fields: {start: startCrtDt, end: endCrtDt}
    date_format: "%Y-%m-%d"
    filter:
      group_column: "接收组"
      groups: ["二线客诉处理组", "常规工单处理组", "回访组一组", "贷后回访组", "12378回访组"]
    data:
      token: ""
      tenementId: ""
      busCode: ""
      sheetType: ""
      busType: ""
      threeTypeId: ""
      isOverTime: ""
      uLevle: ""
      sheetNo: ""
      batchNo: ""
      wayCode: ""
      ext8: ""
      callNumber: ""
      recOrgId: ""
      crtId: ""
      recId: ""
      status: ""
      startCrtDt: ""
      endCrtDt: ""
      content: ""
      ext2: ""
      cusName: ""
      ext3: ""
      urgeStatus: ""
      remark: ""
      fundCode: ""
      cusId: ""
      closeStartTime: ""
      closeEndTime: ""
      serviceLabelCode: ""
      sourceCode: ""
      sceneSecondType: ""
      sceneThirdType: ""
      lastOperateBeginTime: ""
      lastOperateEndTime: ""
      complainDtBegin: ""
      complainDtEnd: ""
      isDesensitization: true
      exports: "sheetNo,busCodeName,allTime,lastOperateTime,ext3,ext2,cusId,cusName,allocateStatus,statusName,sheetTypeName,busTypeName,threeTypeName,crtName,recOrgName,recName,uLevleName,serviceLabelNameList,blackFlagName,sourceName,sceneSecondTypeName,sceneThirdTypeName,complaintName,appeaseName"
```

- [ ] **Step 5: 验证**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe -c "import apscheduler,pandas,openpyxl,xlrd,yaml,websocket; c=yaml.safe_load(open('config.yaml',encoding='utf-8')); assert len(c['subs'])==7 and len(c['detail_modes'])==2; print('cfg ok')"`
Expected: `cfg ok`

- [ ] **Step 6: Commit**

```bash
git add .gitignore config.yaml
git commit -m "chore: project setup, deps, config"
```

---

### Task 2: storage.py

**Files:**
- Create: `storage.py`, `tests/test_storage.py`

**Interfaces:**
- Produces: `storage.insert(source: str, values: dict, data_dir: str) -> None`。`values` 键须匹配 `SCHEMAS[source]`(含"时间")。

- [ ] **Step 1: 写失败测试 `tests/test_storage.py`**

```python
import sqlite3, tempfile
from pathlib import Path
import storage

def main():
    d = tempfile.mkdtemp()
    storage.insert("热线", {"时间":"2026-07-22 09:05","转人工量":10,"接通量":9,
        "排队量":1,"累计呼入量":100,"外呼量":5,"外呼接通量":4}, d)
    conn = sqlite3.connect(Path(d)/"热线.db")
    rows = conn.execute('SELECT 转人工量, 累计呼入量 FROM t').fetchall()
    assert rows == [(10,100)], rows
    # 再插一行验证追加
    storage.insert("热线", {"时间":"2026-07-22 09:10","转人工量":11,"接通量":10,
        "排队量":0,"累计呼入量":110,"外呼量":6,"外呼接通量":5}, d)
    n = conn.execute('SELECT COUNT(*) FROM t').fetchone()[0]
    assert n == 2, n
    print("storage OK")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 运行,确认失败**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_storage.py`
Expected: FAIL `ModuleNotFoundError: storage`

- [ ] **Step 3: 写 `storage.py`**

```python
# -*- coding: utf-8 -*-
"""9 个独立 SQLite,按源名分库,每库一张表 t。"""
import sqlite3
from pathlib import Path

SCHEMAS = {
    "热线":   ["时间","转人工量","接通量","排队量","累计呼入量","外呼量","外呼接通量"],
    "12378":  ["时间","转人工量","接通量","排队量","累计呼入量","外呼量","外呼接通量"],
    "热线明细": ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "常规":   ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "贷后":   ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "12378明细": ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "在线":   ["时间","转人工量","转人工失败","排队","咨询","在线","小休","示忙","话后","就餐","培训","回访"],
    "会话记录": ["时间","转接一组","转接二组","贷后转接组"],
    "工单明细": ["时间","二线客诉处理组","常规工单处理组","回访组一组","贷后回访组","12378回访组"],
}

def insert(source, values, data_dir):
    cols = SCHEMAS[source]
    path = Path(data_dir) / f"{source}.db"
    # ponytail: 每次开/关连接 — 9 路各写各的库,无跨线程共享,简单且无锁竞争
    conn = sqlite3.connect(str(path))
    try:
        col_def = ",".join(f'"{c}" {"TEXT" if c=="时间" else "INTEGER"}' for c in cols)
        conn.execute(f'CREATE TABLE IF NOT EXISTS t ({col_def})')
        quoted = ",".join('"' + c + '"' for c in cols)
        ph = ",".join("?" * len(cols))
        conn.execute(f'INSERT INTO t ({quoted}) VALUES ({ph})', [values[c] for c in cols])
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 运行,确认通过**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_storage.py`
Expected: `storage OK`

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/test_storage.py
git commit -m "feat: storage 9 sqlite per source"
```

---

### Task 3: ws.py

**Files:**
- Create: `ws.py`, `tests/test_ws.py`

**Interfaces:**
- Consumes: `config.yaml` 的 `subs`、`endpoints`、`ws`。
- Produces: `ws.collect_one(sub: dict, cfg: dict) -> dict | None`(返回指标 dict,键匹配 `storage.SCHEMAS[sub["name"]]` 去掉"时间";无数据返回 None)。内部:`_extract_statics`、`_extract_seat(skill)`、`_extract_im`。

- [ ] **Step 1: 写失败测试 `tests/test_ws.py`**

```python
import ws

STATIC = {"cmd":2,"screen":"STATICS","data":{
    "allHrCount":9380,
    "hcAnalysisData":{"allHcCount":8250,"hcSuccessCount":3056,"hourHcCount":11},
    "manualAnalysisData":{"agentCount":4617,"agentSuccessCount":4605,"agentQueueCount":0,
                          "agentFailCount":12,"hourHrCount":82,"pickupRate":"0.9974"}}}
SEAT = {"cmd":2,"screen":"SEAT","data":{
    "agentStatusStatics":{"loginCount":2,"callingCount":1,"idleCount":1,"leaveCount":0,
                          "afterCount":0,"ringCount":0,"busyCount":0},
    "afterOverTimeStatics":[{"agentSplit":"252","agentName":"x"}]}}
IM = {"cmd":2,"screen":"IM_MONITOR","data":{
    "overview":{"todaySessionTotalCnt":3307,"todayQueueFailCnt":1,"queueingCnt":0,"consultingCnt":0},
    "seats":[{"seatStatus":"free"},{"seatStatus":"rest"},{"seatStatus":"offline"},
             {"seatStatus":"notReady"},{"seatStatus":"free"}]}}

def main():
    s = ws._extract_statics(STATIC)
    assert s == {"转人工量":4617,"接通量":4605,"排队量":0,"累计呼入量":9380,
                 "外呼量":8250,"外呼接通量":3056}, s
    seat = ws._extract_seat("252")(SEAT)
    assert seat == {"签入":2,"通话":1,"空闲":1,"离席":0,"话后":0,"振铃":0,"置忙":0}, seat
    assert ws._extract_seat("520")(SEAT) is None  # 跨 skill 过滤
    im = ws._extract_im(IM)
    assert im == {"转人工量":3307,"转人工失败":1,"排队":0,"咨询":0,
                  "在线":2,"小休":1,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}, im
    print("ws OK")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 运行,确认失败**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_ws.py`
Expected: FAIL `ModuleNotFoundError: ws`

- [ ] **Step 3: 写 `ws.py`**

```python
# -*- coding: utf-8 -*-
"""WS 每周期采集:连->发cmd->收首个匹配帧->提取指标->关。提取器复用 v14。"""
import json
import websocket
from websocket import WebSocketTimeoutException

STATUS_MAP = {"free": "在线", "rest": "小休", "notReady": "示忙"}
# ponytail: 话后/就餐/培训/回访 的 seatStatus 映射待从真实值补全(v14 未映射,恒 0)

def _extract_statics(obj):
    try:
        d = obj["data"]; m = d["manualAnalysisData"]; hc = d["hcAnalysisData"]
        return {"转人工量": m["agentCount"], "接通量": m["agentSuccessCount"],
                "排队量": m["agentQueueCount"], "累计呼入量": d["allHrCount"],
                "外呼量": hc["allHcCount"], "外呼接通量": hc["hcSuccessCount"]}
    except Exception:
        return None

def _extract_seat(skill):
    def f(obj):
        try:
            d = obj["data"]; st = d.get("agentStatusStatics")
            if not st:
                return None
            aots = d.get("afterOverTimeStatics") or []
            if aots and aots[0].get("agentSplit") != skill:
                return None  # :7000 夹带跨 skill 推送,跳过
            return {"签入": st["loginCount"], "通话": st["callingCount"],
                    "空闲": st["idleCount"], "离席": st["leaveCount"],
                    "话后": st["afterCount"], "振铃": st["ringCount"],
                    "置忙": st["busyCount"]}
        except Exception:
            return None
    return f

def _extract_im(obj):
    try:
        d = obj["data"]; ov = d.get("overview"); seats = d.get("seats")
        if not ov or not seats:
            return None
        out = {"在线":0,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}
        for s in seats:
            st = s.get("seatStatus")
            if not st or st == "offline":
                continue
            zh = STATUS_MAP.get(st)
            if zh:
                out[zh] += 1
        return {"转人工量": ov["todaySessionTotalCnt"], "转人工失败": ov["todayQueueFailCnt"],
                "排队": ov["queueingCnt"], "咨询": ov["consultingCnt"], **out}
    except Exception:
        return None

def _make_extractor(screen, skill):
    if screen == "STATICS":    return _extract_statics
    if screen == "SEAT":       return _extract_seat(skill)
    if screen == "IM_MONITOR": return _extract_im
    return lambda obj: None

def collect_one(sub, cfg):
    url = cfg["endpoints"][sub["endpoint"]]
    cmd = {"cmd": 1, "screen": sub["screen"], "data": sub["data"]}
    screen = sub["screen"]; skill = sub.get("skill")
    extract = _make_extractor(screen, skill)
    w = cfg["ws"]
    for attempt in range(w["retry"] + 1):
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=w["connect_timeout"])
            ws.send(json.dumps(cmd, ensure_ascii=False))
            ws.settimeout(w["recv_timeout"])
            for _ in range(10):  # 最多收 10 帧找匹配
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    break
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("screen") == screen and obj.get("data"):
                    val = extract(obj)
                    if val is not None:
                        return val
            return None
        except Exception:
            if attempt < w["retry"]:
                continue
            return None
        finally:
            if ws:
                try: ws.close()
                except Exception: pass
    return None
```

- [ ] **Step 4: 运行,确认通过**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_ws.py`
Expected: `ws OK`

- [ ] **Step 5: Commit**

```bash
git add ws.py tests/test_ws.py
git commit -m "feat: ws per-cycle collect + extractors"
```

---

### Task 4: detail.py

**Files:**
- Create: `detail.py`, `tests/test_detail.py`

**Interfaces:**
- Consumes: `cfg["detail_modes"]`、`cfg["secrets"]`、`cfg["detail"]["timeout"]`。
- Produces: `detail.count_groups(df, fcfg) -> dict`、`detail.download_and_count(mode_name, mcfg, secrets, today_str, timeout) -> dict`(键为组名,值计数,0 也含)、`detail._parse_excel(content: bytes) -> DataFrame`。

- [ ] **Step 1: 写失败测试 `tests/test_detail.py`**

```python
import io
import pandas as pd
import openpyxl
import detail

def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["", ""]); ws.append(["", ""])  # 两行空白
    for r in rows: ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()

def main():
    df = pd.DataFrame({"渠道来源":["电话呼入呼入","在线客服呼入呼入","电话呼入"],
                       "处理组别":["转接一组","转接二组","转接一组"]})
    fcfg = {"channel_column":"渠道来源","channels":["电话呼入呼入","在线客服呼入呼入"],
            "group_column":"处理组别","groups":["转接一组","转接二组","贷后转接组"]}
    c = detail.count_groups(df, fcfg)
    assert c == {"转接一组":1,"转接二组":1,"贷后转接组":0}, c
    # xlsx 解析:第三行表头
    df2 = detail._parse_excel(_xlsx([["渠道来源","处理组别"],["电话呼入呼入","转接一组"]]))
    assert list(df2.columns) == ["渠道来源","处理组别"], list(df2.columns)
    assert len(df2) == 1, len(df2)
    print("detail OK")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 运行,确认失败**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_detail.py`
Expected: FAIL `ModuleNotFoundError: detail`

- [ ] **Step 3: 写 `detail.py`**

```python
# -*- coding: utf-8 -*-
"""requests 明细下载 + Excel 解析 + 按组计数。不保存 Excel。"""
import io
import requests
import pandas as pd

def count_groups(df, fcfg):
    d = df
    if fcfg.get("channel_column"):
        d = d[d[fcfg["channel_column"]].isin(fcfg["channels"])]
    d = d[d[fcfg["group_column"]].isin(fcfg["groups"])]
    cnt = d.groupby(fcfg["group_column"]).size().to_dict()
    return {g: int(cnt.get(g, 0)) for g in fcfg["groups"]}

def _parse_excel(content):
    if content[:4] == b'PK\x03\x04':
        engine = "openpyxl"
    elif content[:8] == b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1':
        engine = "xlrd"
    else:
        raise ValueError(f"非Excel文件: {content[:20]!r}")
    return pd.read_excel(io.BytesIO(content), header=2, engine=engine)

def download_and_count(mode_name, mcfg, secrets, today_str, timeout):
    data = dict(mcfg["data"])
    data["token"] = secrets["token"]
    data["tenementId"] = secrets["tenementId"]
    dv = today_str if mcfg["date_format"] == "%Y-%m-%d" else f"{today_str} 00:00:00"
    data[mcfg["date_fields"]["start"]] = dv
    data[mcfg["date_fields"]["end"]] = dv
    resp = requests.post(mcfg["url"], json=data, timeout=timeout)
    resp.raise_for_status()
    df = _parse_excel(resp.content)
    return count_groups(df, mcfg["filter"])
```

- [ ] **Step 4: 运行,确认通过**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_detail.py`
Expected: `detail OK`

- [ ] **Step 5: Commit**

```bash
git add detail.py tests/test_detail.py
git commit -m "feat: requests detail download + count"
```

---

### Task 5: notify.py

**Files:**
- Create: `notify.py`

**Interfaces:**
- Produces: `notify.send_alert(message: str, **ctx) -> None`、`notify.take_screenshot(url=None) -> None`。当前 stub(仅记日志),实现后续补充。

- [ ] **Step 1: 写 `notify.py`**

```python
# -*- coding: utf-8 -*-
"""告警/截图入口 stub,实现后续补充(参考 v14 企微 webhook / Playwright)。"""
import logging
log = logging.getLogger("autowfm")

def send_alert(message, **ctx):
    log.info(f"[alert stub] {message} {ctx}")
    # TODO: v14 企微 webhook _webhook/_send_md

def take_screenshot(url=None):
    log.info(f"[screenshot stub] url={url}")
    # TODO: v14 Playwright 截 localhost:8080
    return None
```

- [ ] **Step 2: 验证导入**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe -c "import notify; notify.send_alert('test', k=1); notify.take_screenshot('http://x'); print('notify ok')"`
Expected: `notify ok`

- [ ] **Step 3: Commit**

```bash
git add notify.py
git commit -m "feat: notify alert/screenshot stubs"
```

---

### Task 6: scheduler.py

**Files:**
- Create: `scheduler.py`, `tests/test_guard.py`

**Interfaces:**
- Consumes: `ws.collect_one`、`detail.download_and_count`、`storage.insert`、`cfg`。
- Produces: `scheduler.start(cfg) -> None`(阻塞)、`scheduler._in_window(cfg, now=None) -> bool`。

- [ ] **Step 1: 写失败测试 `tests/test_guard.py`**

```python
from datetime import datetime
from zoneinfo import ZoneInfo
import scheduler
TZ = ZoneInfo("Asia/Shanghai")
cfg = {"schedule":{"window_start":"09:00","window_end":"21:00","timezone":"Asia/Shanghai"}}
def at(h,m): return datetime(2026,7,22,h,m,tzinfo=TZ)

def main():
    assert scheduler._in_window(cfg, at(9,0)) is False
    assert scheduler._in_window(cfg, at(9,5)) is True
    assert scheduler._in_window(cfg, at(12,30)) is True
    assert scheduler._in_window(cfg, at(21,0)) is True
    assert scheduler._in_window(cfg, at(21,5)) is False
    print("guard OK")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 运行,确认失败**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_guard.py`
Expected: FAIL `ModuleNotFoundError: scheduler`

- [ ] **Step 3: 写 `scheduler.py`**

```python
# -*- coding: utf-8 -*-
"""APScheduler:WS 任务 + requests 任务,窗口 guard,各自线程池。"""
import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
import ws as ws_mod
import detail as detail_mod
import storage
import notify

log = logging.getLogger("autowfm")

def _parse_hhmm(s):
    h, m = s.split(":"); return int(h) * 60 + int(m)

def _in_window(cfg, now=None):
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    mins = now.hour * 60 + now.minute
    return _parse_hhmm(cfg["schedule"]["window_start"]) < mins <= _parse_hhmm(cfg["schedule"]["window_end"])

def _now(cfg):
    return datetime.datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))

def ws_job(cfg, pool):
    if not _in_window(cfg):
        return
    now_str = _now(cfg).strftime("%Y-%m-%d %H:%M")
    futs = {pool.submit(ws_mod.collect_one, s, cfg): s for s in cfg["subs"]}
    ok = fail = 0
    for f in as_completed(futs):
        s = futs[f]
        try:
            val = f.result()
            if val is None:
                fail += 1; log.warning(f"[WS] {s['name']} 无数据"); continue
            storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
            ok += 1
        except Exception:
            fail += 1; log.exception(f"[WS] {s['name']} 异常")
    log.info(f"[WS] 周期完成 ok={ok} fail={fail}")

def detail_job(cfg, pool):
    if not _in_window(cfg):
        return
    now = _now(cfg)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    today = now.strftime("%Y-%m-%d")
    futs = {pool.submit(detail_mod.download_and_count, n, m, cfg["secrets"], today, cfg["detail"]["timeout"]): n
            for n, m in cfg["detail_modes"].items()}
    ok = fail = 0
    for f in as_completed(futs):
        n = futs[f]
        try:
            counts = f.result()
            storage.insert(n, {"时间": now_str, **counts}, cfg["storage"]["dir"])
            ok += 1
        except Exception:
            fail += 1; log.exception(f"[detail] {n} 异常")
    log.info(f"[detail] 周期完成 ok={ok} fail={fail}")
    # ponytail: 告警/截图入口已留,调用时机后续补充
    # notify.send_alert(...); notify.take_screenshot(cfg.get("screenshot_url"))

def start(cfg):
    tz = cfg["schedule"]["timezone"]
    ws_pool = ThreadPoolExecutor(max_workers=7, thread_name_prefix="ws")
    det_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="detail")
    start_date = datetime.datetime.now(ZoneInfo(tz)).replace(hour=9, minute=5, second=0, microsecond=0)
    trig = IntervalTrigger(minutes=cfg["schedule"]["interval_minutes"], start_date=start_date, timezone=tz)
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(ws_job, trig, args=[cfg, ws_pool], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="ws")
    sched.add_job(detail_job, trig, args=[cfg, det_pool], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="detail")
    log.info("调度器启动")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
        ws_pool.shutdown(wait=False)
        det_pool.shutdown(wait=False)
```

- [ ] **Step 4: 运行,确认通过**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\test_guard.py`
Expected: `guard OK`

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_guard.py
git commit -m "feat: scheduler two jobs + window guard"
```

---

### Task 7: main.py

**Files:**
- Create: `main.py`

**Interfaces:**
- Produces: `main.load_cfg(path="config.yaml") -> dict`、`main.setup_logging(cfg) -> None`、`main.main()`。

- [ ] **Step 1: 写 `main.py`**

```python
# -*- coding: utf-8 -*-
"""入口:加载配置、日志、启动调度器。"""
import logging, sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import yaml
import scheduler

def load_cfg(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def setup_logging(cfg):
    Path("logs").mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    p = cfg.get("logging", {}).get("path")
    if p:
        Path(p).parent.mkdir(exist_ok=True)
        handlers.append(TimedRotatingFileHandler(p, when="midnight", backupCount=30, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)

def main():
    cfg = load_cfg()
    setup_logging(cfg)
    scheduler.start(cfg)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证导入(不启动调度)**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe -c "import main; c=main.load_cfg(); assert len(c['subs'])==7; print('main ok')"`
Expected: `main ok`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: main entry"
```

---

### Task 8: Integration smoke

**Files:**
- Create: `tests/smoke.py`

- [ ] **Step 1: 写 `tests/smoke.py`(一次性跑全链路,不走调度)**

```python
# -*- coding: utf-8 -*-
"""一次性跑 9 路(不走调度),验证采集+入库。"""
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import main as M
import ws as W
import detail as D
import storage

def main():
    cfg = M.load_cfg(); M.setup_logging(cfg)
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M"); today = now.strftime("%Y-%m-%d")
    print("== WS ==")
    with ThreadPoolExecutor(max_workers=7) as p:
        futs = {p.submit(W.collect_one, s, cfg): s for s in cfg["subs"]}
        for f in as_completed(futs):
            s = futs[f]; val = f.result()
            if val:
                storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
                print(f"  OK   {s['name']}: {val}")
            else:
                print(f"  FAIL {s['name']}")
    print("== detail ==")
    with ThreadPoolExecutor(max_workers=2) as p:
        futs = {p.submit(D.download_and_count, n, m, cfg["secrets"], today, cfg["detail"]["timeout"]): n
                for n, m in cfg["detail_modes"].items()}
        for f in as_completed(futs):
            n = futs[f]
            try:
                c = f.result()
                storage.insert(n, {"时间": now_str, **c}, cfg["storage"]["dir"])
                print(f"  OK   {n}: {c}")
            except Exception as e:
                print(f"  FAIL {n}: {e}")
    print("done")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 运行 smoke(需在网络可达、业务时段)**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe tests\smoke.py`
Expected: 9 路各打印 `OK` + 指标/计数;无 `FAIL`。若 detail 报非 Excel/HTTP 错,检查 token 是否过期或返回内容。

- [ ] **Step 3: 验证 9 库有数据**

Run:
```bash
D:\PythonProject\AutoWFM\.venv\Scripts\python.exe -c "import sqlite3,glob; [print(f, sqlite3.connect(f).execute('SELECT COUNT(*) FROM t').fetchone()[0]) for f in glob.glob('data/*.db')]"
```
Expected: 9 个 `.db` 各 `1`(或 smoke 跑多次则更多)。

- [ ] **Step 4: 启动正式进程(可选,业务时段验证)**

Run: `D:\PythonProject\AutoWFM\.venv\Scripts\python.exe main.py`
Expected: 日志 `调度器启动`,到下一 5 分钟整点(9-21 内)各周期打印 `[WS] 周期完成` 与 `[detail] 周期完成`。Ctrl+C 退出。

- [ ] **Step 5: Commit**

```bash
git add tests/smoke.py
git commit -m "test: integration smoke"
```

---

## Notes

- spec §5 的 `hours=range(9,22)` 对 `IntervalTrigger` 无效;窗口完全由 `_in_window` guard 保证(`540 < mins <= 1260`)。IntervalTrigger 24 小时每 5 分钟触发,窗口外 guard no-op(开销可忽略)。
- `pd.read_excel(header=2)` 依赖前两行空白;若真实文件首两行非全空,smoke(Task 8)会暴露,改 `skiprows=2`。
- 在线 `话后/就餐/培训/回访` 映射待真实 `seatStatus` 值补全(见 ws.py `# ponytail` 注释)。
- token 过期:smoke 若 detail 全 FAIL 且报非 Excel,即 token 失效,需换 `config.yaml` 的 `secrets.token`。
