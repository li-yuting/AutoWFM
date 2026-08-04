# AutoWFM 数据采集与统计 - 设计

- 日期: 2026-07-22
- 参考: `D:\PythonProject\hfqwfm\everyday\hfq_spider_v14.py`(WS 部分前身)

## 1. 目标

每天 **(9:00, 21:00]** 窗口内,每 5 分钟采集 7 路 WebSocket 监控指标 + 2 路 requests 明细计数,分别存入 9 个独立 SQLite 库(时间序列)。各路独立,不合并。

- 窗口:开区间含尾。首拍 09:05,末拍 21:00,共 144 拍/天。
- 告警发送/截图:**保留入口(stub)**,实现后续补充;报表暂不做。

## 2. 范围

- WS 采集(7 路):连接、提取指标、入库。
- requests 明细(2 路):下载当日 Excel、解析、按组计数、入库。
- 统计 = 各路按组/按指标的时间序列,各自独立产出,**不跨库合并**。
- 告警/截图:保留发送告警信息与截图的入口(stub),具体逻辑后续补充。

## 3. 架构

长驻进程,`APScheduler` 驱动。窗口内每 5 分钟触发**两个独立任务**,各自线程池,互不影响(一个慢/挂不拖累另一个):

- **任务 A - WS 采集**:7 路并发(线程池 7)。
- **任务 B - requests 明细+计数**:2 路并发(线程池 2)。

两任务同一 5 分钟节拍、同一窗口 guard,各自 `max_instances=1` + `coalesce=True` + 小 `misfire_grace_time`(中途启动不补跑,某拍超时跳过本拍不堆积)。

运行时:同步 `websocket-client` + `requests` + 线程池。不用 gevent。

## 4. 模块结构

```
AutoWFM/
  config.yaml     # 端点、调度窗口、subs、requests模式、超时、库目录
  main.py         # 入口:加载配置,启动调度器,优雅退出
  scheduler.py    # APScheduler:任务A + 任务B,窗口guard,misfire策略
  ws.py           # WS采集:per-cycle connect/recv + 指标提取(_extract_statics/seat/im)
  detail.py       # requests明细:POST + Excel解析(header=2) + 过滤计数
  storage.py      # 9个SQLite写入(按source分库,建表,插入)
  notify.py       # 告警发送/截图入口(stub,后续补充)
  data/           # 9个 .db
  logs/
  spike/          # 探测脚本(ws_probe.py,非生产)
```

## 5. 调度

- 触发器:`IntervalTrigger(minutes=5)`,`start_date` 锚 09:05(拍点对齐 :05/:10/.../:00)。
- 小时过滤:`hours=range(9, 22)`(9..21),减少窗口外唤醒。
- 边界 guard(关键,表达半开区间):任务内 `mins = hour*60+minute`,仅当 `start_mins < mins <= end_mins` 执行。`start_mins`/`end_mins` 由 config `window_start`(开)/`window_end`(闭)解析,默认 540(09:00,排除)/1260(21:00,含)。
- 效果:首拍 09:05,末拍 21:00,窗口外 no-op。

## 6. WS 采集(任务 A)

- 端点:
  - `OTHER_URL = ws://monitor-datawarehouse-cloud.weicai.com.cn:7000/customer/monitor`(热线/12378/4路SEAT)
  - `ONLINE_URL = ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor`(在线)
- **无需认证**(实测直连成功,IP 白名单)。`get_auth_headers` hook 保留,返回 `{}`。
- 每路每周期:`create_connection(url, timeout)` -> `send(json.dumps(subscribe_msg))` -> 收**首个 `screen==请求 且 data 非空`的帧**(避开空帧/跨 screen 推送,带超时) -> 提取指标 -> 写该路 SQLite -> `close()`。
- 连接失败重试 1 次(可配)。单路失败只记日志,不影响其他 6 路。
- 指标提取复用 v14 的 `_extract_*` 逻辑(见 §9 字段映射)。SEAT 路按 `agentSplit==skill` 过滤跨 skill 推送。

### WS 订阅定义(与 v14 一致)

```
SEAT_DATA = {skillCode, pickUpRankDeptId, busyRankDeptId,
             afterOverTimeDeptId, afterOverTimeStatiscsDeptId, agentStatusDeptId}
```

| 名称 | 端点 | screen | skillCode | 备注 |
|---|---|---|---|---|
| 热线 | OTHER | STATICS | "" | numberType=HFQ_OFFICIAL |
| 12378 | OTHER | STATICS | "" | numberType=SERVICE_12378 |
| 热线明细 | OTHER | SEAT | 252 | |
| 常规转接明细 | OTHER | SEAT | 520 | |
| 贷后转接明细 | OTHER | SEAT | 958 | |
| 12378明细 | OTHER | SEAT | 847 | agentStatusDeptId=q40YvMUf... |
| 在线 | ONLINE | IM_MONITOR | "" | |

订阅消息:`{"cmd": 1, "screen": <screen>, "data": <完整 data>}`。

## 7. requests 明细 + 计数(任务 B)

复用参考脚本逻辑,但**不保存 Excel**,直接转 DataFrame 计数。

- 两路:`会话记录`、`工单明细`。每路:拷贝 `data` -> 按 `date_fields` 把起止日期设为**今天**(会话记录 `"%Y-%m-%d 00:00:00"`,工单明细 `"%Y-%m-%d"`) -> POST 取字节。
- 魔数判格式:`PK\x03\x04`->xlsx(openpyxl),`\xD0\xCF\x11\xE0...`->xls(xlrd)。非 Excel 记错误。
- 解析:`pd.read_excel(BytesIO(content), header=2, engine=...)`(`header=2` 即第三行为表头,前两行空白跳过)。表头为**中文**(渠道来源/处理组别/接收组)。
- 过滤计数(列名/筛值均可配):
  - **会话记录**:筛 `渠道来源 ∈ {电话呼入呼入, 在线客服呼入呼入}` 且 `处理组别 ∈ {转接一组, 转接二组, 贷后转接组}`,按 `处理组别` 计数。
  - **工单明细**:筛 `接收组 ∈ {二线客诉处理组, 常规工单处理组, 回访组一组, 贷后回访组, 12378回访组}`,按 `接收组` 计数。
- 计数写该路 SQLite。配置内组别计数为 0 也输出一列(时间序列规整)。
- 超时 60s(可配)。单路失败只记日志,不影响另一路也不影响 WS 任务。

### requests 模式配置(端口点 / date_fields / data)

- 会话记录:`https://callcenter-crm.weicai.com.cn/api/sheet/callLog/exportCL`,`date_fields={staStartDt, endStartDt}`,`date_format="%Y-%m-%d %H:%M:%S"`,`exports=...`,`token`/`tenementId` 同参考脚本。
- 工单明细:`https://callcenter-crm.weicai.com.cn/api/sheet/list/exportCLSheet`,`date_fields={startCrtDt, endCrtDt}`,`date_format="%Y-%m-%d"`,`exports=...`,`token`/`tenementId` 同参考脚本。
- 完整 `data` 字典(含全部筛选字段)从参考脚本逐字搬运进 config;`token`/`tenementId` 为敏感项,单列 secrets。

## 8. 存储:9 个独立 SQLite

`data/` 下 9 个 `.db`,每库一张表,5 分钟一行时间序列。各库独立连接、互不抢锁。

| 库 | 表字段(均含 `时间`) |
|---|---|
| 热线.db | 转人工量,接通量,排队量,累计呼入量,外呼量,外呼接通量 |
| 12378.db | 转人工量,接通量,排队量,累计呼入量,外呼量,外呼接通量 |
| 热线明细.db | 签入,通话,空闲,离席,话后,振铃,置忙 |
| 常规转接明细.db | 签入,通话,空闲,离席,话后,振铃,置忙 |
| 贷后转接明细.db | 签入,通话,空闲,离席,话后,振铃,置忙 |
| 12378明细.db | 签入,通话,空闲,离席,话后,振铃,置忙 |
| 在线.db | 转人工量,转人工失败,排队,咨询,在线,小休,示忙,话后,就餐,培训,回访 |
| 会话记录.db | 转接一组,转接二组,贷后转接组 |
| 工单明细.db | 二线客诉处理组,常规工单处理组,回访组一组,贷后回访组,12378回访组 |

- `时间` = 该拍采集时刻。其余列为该路指标/计数。
- **不保存**:ivrCount/ivrHangUpCount、hourHcCount、21 点小时数组、SEAT 的 95 坐席明细、IM 的 130 坐席明细、原始 raw 帧(只存提取的指标)。

## 9. 字段映射

**statics(热线/12378)**,取 `data`:
- 转人工量 = `manualAnalysisData.agentCount`
- 接通量 = `manualAnalysisData.agentSuccessCount`
- 排队量 = `manualAnalysisData.agentQueueCount`
- 累计呼入量 = `allHrCount`
- 外呼量 = `hcAnalysisData.allHcCount`
- 外呼接通量 = `hcAnalysisData.hcSuccessCount`

**seat(4 路明细)**,取 `data.agentStatusStatics`:
- 签入=`loginCount`、通话=`callingCount`、空闲=`idleCount`、离席=`leaveCount`、话后=`afterCount`、振铃=`ringCount`、置忙=`busyCount`

**im(在线)**,取 `data.overview` + `data.seats`:
- 转人工量=`overview.todaySessionTotalCnt`、转人工失败=`todayQueueFailCnt`、排队=`queueingCnt`、咨询=`consultingCnt`
- 在线/小休/示忙/话后/就餐/培训/回访 = `seats[].seatStatus` 计数;`free->在线`、`rest->小休`、`notReady->示忙`、`offline` 不计。`话后/就餐/培训/回访` 的 seatStatus 映射**实现时看真实值补全**(v14 未映射,恒为 0,待修正)。

## 10. 配置(config.yaml)

```yaml
endpoints:
  online: "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
  other:  "ws://monitor-datawarehouse-cloud.weicai.com.cn:7000/customer/monitor"
schedule:
  interval_minutes: 5
  window_start: "09:00"   # 开区间
  window_end:   "21:00"   # 闭区间
ws:
  connect_timeout: 12
  recv_timeout: 8
  retry: 1
detail:
  timeout: 60
  modes:
    会话记录:
      url: "https://callcenter-crm.weicai.com.cn/api/sheet/callLog/exportCL"
      date_fields: {start: staStartDt, end: endStartDt}
      date_format: "%Y-%m-%d %H:%M:%S"
      data: { ...完整 data,含 token/tenementId... }
      filter:
        channel_column: "渠道来源"
        channels: ["电话呼入呼入", "在线客服呼入呼入"]
        group_column: "处理组别"
        groups: ["转接一组", "转接二组", "贷后转接组"]
    工单明细:
      url: "https://callcenter-crm.weicai.com.cn/api/sheet/list/exportCLSheet"
      date_fields: {start: startCrtDt, end: endCrtDt}
      date_format: "%Y-%m-%d"
      data: { ...完整 data,含 token/tenementId... }
      filter:
        group_column: "接收组"
        groups: ["二线客诉处理组", "常规工单处理组", "回访组一组", "贷后回访组", "12378回访组"]
secrets:
  token: "USER_TOKEN_KEY..."
  tenementId: "201804131002426760327BiIC"
storage:
  dir: "data"
logging:
  path: "logs/autowfm.log"
```

`subs`(7 路)也进 config,每路含 name/endpoint/screen/完整 data(用 YAML 锚点表达 SEAT_DATA 基底,见 §6)。

## 11. 错误处理与可观测

- 单路隔离失败:WS 单路失败(超时/连接/解析)只记日志;requests 单路失败(HTTP 非 200/非 Excel/解析异常)只记日志。互不影响。
- WS 连接失败重试 1 次。
- `logging` 滚动日志:每拍汇总(A 任务 7 路成功/失败、B 任务 2 路成功/失败、耗时)。
- 进程级:Windows 用 NSSM/任务计划程序长驻 + 崩溃自启。

### 告警/截图入口(stub)
- `notify.send_alert(message, **ctx)`:发送告警信息入口,当前 stub(仅记日志),实现后续补充(参考 v14 企微 webhook)。
- `notify.take_screenshot(url=None)`:截图入口,当前 stub(返回 None),实现后续补充(参考 v14 Playwright 截 localhost:8080)。
- 调用时机(何时告警/截图)与报表逻辑:后续补充。

## 12. 测试

- `ws.py` 提取器单测:喂探测到的真实帧样本,断言各字段值(用 `spike/` 抓样本)。
- `detail.py` 单测:喂样本 Excel 字节(xls/xlsx 各一),断言过滤计数。
- `storage.py` 单测:9 库建表 + 插入 + 查询往返。
- 调度测:窗口 guard(9:00 排除、9:05 含、21:00 含、21:05 排除)。
- 集成测:mock WS/HTTP 回放,跑全链路。

## 13. 依赖(补装)

`APScheduler`、`pandas`、`openpyxl`、`xlrd`。已装:`websocket-client`、`requests`、`PyYAML`。Python 3.14.6。

## 14. 开放项

1. **在线 seatStatus 映射**:`话后/就餐/培训/回访` 的 seatStatus 值待实现时从真实数据确认并补全(v14 漏映射)。
2. **token 过期**:现为固定值;若会过期需加刷新机制。

## 15. 与 v14 的差异

| | v14 | AutoWFM |
|---|---|---|
| 连接 | 持久连接+后台线程+latest缓存 | 每周期连一次(取首帧) |
| 存储 | 4 个 CSV | 9 个独立 SQLite |
| 指标 | statics 3 字段 | statics 6 字段(加 累计呼入量/外呼量/外呼接通量) |
| 告警/截图 | 有 | 保留入口(stub),实现后续补充 |
| 报表/webhook | 有 | 暂不做 |
| requests 明细+统计 | 无 | 有(2 路) |
