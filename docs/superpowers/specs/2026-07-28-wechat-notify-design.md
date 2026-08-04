# 企微消息推送与告警 - 设计文档

- 日期: 2026-07-28
- 范围: 实现 `collector/notify.py` 预留的企微 webhook 推送入口(常规定时报表 + 排队告警),并把触发逻辑接入 `collector/scheduler.py`。
- 参考: `D:\PythonProject\hfqwfm\everyday\hfq_spider_v14.py`(v14 的 `_webhook`/`_send_md`/`_send_img`/`_send_alert`/`_screenshot` 模式)。

## 1. 目标与范围

补全 `collector/notify.py` 两个 stub(`send_alert` / `take_screenshot`),实现两类消息:

1. **常规定时推送(markdown)**: 程序运行期间,分钟数为 `00/15/30/45` 时推送。分两条消息:
   - 第一条 **一线(热线+在线)** -> `main_key`。
   - 第二条 **二线(常规转接组+贷后转接组+12378)** -> `secondary_key`。
   - 两条 markdown 发完后,对看板截一张图,base64 图片分别发到 `main_key` 与 `secondary_key`。
2. **排队告警(text,因 markdown 不支持 @)**: 每次 WS 采集周期(每 5 分钟)检查 热线/在线/12378 排队,超阈值且(热线/12378)空闲<排队则告警,文本消息 @ 对应手机号。

**不在范围内**: 不改采集/存储逻辑;不改看板;不引入去重(v14 亦无);告警每周期都发(用户确认)。

## 2. 架构(Approach A)

新增第 3 个 APScheduler 作业 `push_job`(`CronTrigger(minute="0,15,30,45")`),与 `ws_job`/`detail_job` 同调度器、各自独立线程池/实例。告警检查挂在 `ws_job` 采集循环之后。所有外部 IO 与文案组装集中在 `collector/notify.py`,**不 import dashboard**(`collector 写 / dashboard 读` 分层不变)。

组件:

1. **`collector/notify.py`**(由 stub 扩展):
   - `latest_snapshot(data_dir, source, date_str)` -> 当天 `时间` 最大的一行(dict)或 `None`。
   - `forecast_at(data_dir, line, now_str)` -> `预估流入量.csv` 中 `线路==line` 且 `时间==now_str` 的 `累计预估量`;未命中/超 CSV 范围 -> `0`。CSV `时间` 列格式与 `dashboard/queries.load_forecast` 的解析保持一致(实现时对照 `load_forecast` 确认格式,避免漂移)。
   - `build_firstline_msg(data_dir, now_str)` / `build_secondline_msg(data_dir, now_str)` -> markdown 字符串。
   - `_webhook(key, payload)` / `_send_md(text, key)` / `_send_img(path, key)` / `_send_text(key, mobiles, msg)` —— 移植自 v14。
   - `take_screenshot(url)` -> Playwright 截图 png 路径或 `None`。
   - `send_report(cfg)` —— 定时推送入口: 窗口挡 -> 读快照 -> 组两条 markdown -> 发 -> 截图 -> 图片发两路。
   - `check_alerts(cfg)` —— 告警入口: 读 latest -> 逐源判阈值 -> 发 text 告警。
2. **`collector/scheduler.py`**: 加 `push_job`;`ws_job` 采集循环后调 `notify.check_alerts(cfg)`。
3. **`config.yaml`**: 新增 `notify:` 块(见 §6)。

**为何独立 job 而非折进 ws_job**: 截图+HTTP 耗时约 10s,折进 `ws_job`(max_instances=1, coalesce=True)会拖慢下一轮采集;独立 CronTrigger 解耦节奏,且语义清晰(推送 ≠ 采集)。

## 3. 数据流 - 字段到来源映射

所有数据取当天**最新快照**(`时间` 最大的行)+ 当前时刻 CSV 预测。累计量(转人工量/接通量/转接量/工单量)读最新累计行;坐席量(签入/通话/空闲/…)读最新瞬时快照。与看板共用 9 db 模型。

### 消息1 - 一线(-> main_key)

| 字段 | 来源 | 备注 |
|---|---|---|
| 热线 预测量 | `预估流入量.csv` 线路=热线 @ now | `累计预估量` |
| 热线 转人工量/接通量/排队量 | `热线.db` latest | 接通量直接取列 |
| 热线 流入率 | 转人工量/预测量 | 预测量=0 -> `0.0%` |
| 热线 接通率 | 接通量/转人工量 | 转人工量=0 -> `0.0%` |
| 热线 签入/通话/话后/空闲/置忙 | `热线明细.db` latest | 5 项坐席 |
| 在线 预测量 | CSV 线路=在线 @ now | `累计预估量` |
| 在线 转人工量/排队/咨询/在线/回访/话后/小休/示忙/就餐 | `在线.db` latest | 排队列名是 `排队` |
| 在线 接通量 | 转人工量 − 转人工失败 | `转人工失败` 列 |
| 在线 流入率/接通率 | 同热线算法 | |

### 消息2 - 二线(-> secondary_key)

| 组 | 转接量 | 工单量 | 坐席(签入/通话/话后/空闲/置忙/离席/振铃) |
|---|---|---|---|
| 常规转接组 | `会话记录.db`(转接一组+转接二组) | `工单明细.db`.回访组一组 | `常规.db` |
| 贷后转接组 | `会话记录.db`.贷后转接组 | `工单明细.db`.贷后回访组 | `贷后.db` |
| 12378 | — (无预测量/转接量行) | — | 转人工量/接通量/排队量 ← `12378.db`;坐席 ← `12378明细.db`;接通率=接通量/转人工量 |

> 工单量映射依据 spec §4.1: 常规二线.工单量=回访组一组, 贷后二线.工单量=贷后回访组(用户确认)。转接量: 常规二线=会话记录(转接一组+转接二组), 贷后二线=会话记录.贷后转接组。

### 缺数据处理(沿用 v14)

组的坐席库(或 12378 的主库)当天无行 -> **跳过该 section**(早间首次推送前),不打印 0。热线完全无数据 -> 跳过消息1。二线各组同理。

### 告警(在 ws_job,每周期)

读刚落库的最新行:
- 热线: `热线.db.排队量` + `热线明细.db.空闲` -> `排队量>10 且 空闲<排队量` 则告警。
- 在线: `在线.db.排队` -> `排队>20` 则告警(**不加**空闲条件,用户确认;在线无"空闲"指标)。
- 12378: `12378.db.排队量` + `12378明细.db.空闲` -> `排队量>1 且 空闲<排队量` 则告警。

阈值边界用 `>=`(排队达到阈值即告警,等于也触发)。空闲<排队 仍为严格 `<`。每周期条件成立就发(无去重,用户确认)。

**窗口内才告警(防陈旧误报)**: `check_alerts` 只对"本周期在窗口内、数据是新鲜的"源判阈值。热线/在线 走全局窗口(它们与 `ws_job` 同窗口,`ws_job` 出窗口即早返回,`check_alerts` 不会被调);**12378 走自己的 `schedule`**(周末 `(9,18]` 比全局 `(9,21]` 早关)——周末 18:00 后 12378 不再采集,此时即便 `ws_job` 仍在跑,也**跳过 12378 告警**,避免用 18:00 的陈旧排队触发误报。判定复用 `scheduler._in_window(cfg, sub, now)`,传 12378 的 sub。

## 4. 消息与告警格式

### 消息1(markdown -> main_key)

```
# 当前时间: 2026-07-28 11:00    
统计监控`热线`:     
>预测量: 1187, 转人工量：1108    
>流入率：93.34%    
>接通量：1106, 接通率：99.82%    
>排队量：0    
>签入人数：87     
>通话人数：38, 话后人数：5    
>空闲人数：42, 置忙人数：0    

统计监控`在线`:     
>预测量: 811, 转人工量：826    
>流入率：101.85%    
>接通量：824, 接通率：99.76%    
>排队量：0    
>正在咨询人数：75    
>在线人数：32, 回访人数：0    
>话后人数：0, 小休人数：5    
>示忙人数：1, 就餐人数：0    
```

### 消息2(markdown -> secondary_key)

```
# 当前时间：2026-07-28 11:15    
签入情况`常规转接组`:     
>转接量：100, 工单量：150    
>签入人数：17     
>通话人数：8, 话后人数：3    
>空闲人数：6, 置忙人数：0    
>离席人数：0, 振铃人数：0    

签入情况`贷后转接组`:     
>转接量：100, 工单量：150    
>签入人数：18     
>通话人数：5, 话后人数：4    
>空闲人数：9, 置忙人数：0    
>离席人数：0, 振铃人数：0    

统计监控`12378`:     
>转人工量：44    
>接通量：44, 接通率：100.00%    
>排队量：0    
>签入人数：6     
>通话人数：1, 话后人数：0    
>空闲人数：5, 置忙人数：0    
```

格式细则: 每行末尾 4 空格 = markdown 换行;热线/在线、各二线组之间空一行;预测量/转人工量取整;流入率/接通率保留 2 位小数(`{:.2f}%`);常规/贷后 section 首行为 `>转接量：X, 工单量：Y`(v14 无此行,本次新增)。

### 告警(text,每组一条独立消息以便 @)

- 热线(-> main_key, @hotline): `⚠️ 排队告警 {now}\n热线排队：{q} 人（阈值 10，空闲 {idle}）`
- 在线(-> main_key, @online): `⚠️ 排队告警 {now}\n在线排队：{q} 人（阈值 20）` (无空闲项)
- 12378(-> secondary_key, @12378): `⚠️ 12378排队告警 {now}\n12378排队：{q} 人（阈值 1，空闲 {idle}）`

每条 text 消息带 `mentioned_mobile_list` = 对应手机号列表。

## 5. 截图

`take_screenshot(url)`:
- Playwright headless chromium,viewport 1920×1080。
- `goto(url, wait_until="networkidle", timeout=30000)`。
- `wait_for_timeout(4000)` 等 Chart.js 渲染(本看板无 v14 的 `updateTime` 标记,用固定延迟)。
- `screenshot(path=path, full_page=True)` -> `data/screenshot.png`;失败返回 `None`。
- 两条 markdown 发完后,**一张截图**分别 `_send_img` 到 main_key 与 secondary_key。

一次性准备: `playwright install chromium`(playwright 包已在 venv,浏览器二进制可能未装)。

## 6. config.yaml 新增

```yaml
notify:
  screenshot_url: "http://localhost:5001/"   # 改8080后换这里
  push_minutes: [0, 15, 30, 45]
  webhook:
    main_key: "6702efeb-5787-4285-948d-93ebb6f29c7d"      # 一线 + 截图
    secondary_key: "c816bbf5-c34c-4b7e-93b3-578a891e68dd"  # 二线 + 截图
  alert:
    hotline_queue: 10
    online_queue: 20
    queue_12378: 1
    recipients:
      hotline: ["17629050914", "18829270926"]
      online: ["17629050914", "18821657478"]
      "12378": ["17629050914", "18629552489"]
```

## 7. 调度接入

- `push_job`: `CronTrigger(minute="0,15,30,45")`,job 内复用 `_in_window(cfg, None)` 按全局窗口(9,21]挡(与 ws_job 24/7 触发+窗口内才动手一致);`max_instances=1, coalesce=True, misfire_grace_time=60`。调 `notify.send_report(cfg)`。
- `ws_job`: 采集循环 `as_completed` 完成后调 `notify.check_alerts(cfg)`(外层 try/except,不阻断采集)。

## 8. 错误处理

核心原则: **通知失败绝不能拖垮采集**。
- `_webhook` 已 try/except,返回错误串不抛异常(v14 风格);msg1 失败仍继续 msg2 + 截图。
- 截图失败(看板未起 / chromium 未装) -> log + 跳过图片,markdown 照发。
- `send_report` / `check_alerts` 外层再裹 try/except + log,保证 ws_job/push_job 不因通知异常中断。

## 9. 测试(`tests/test_notify.py`,plain assert,无 pytest)

- `latest_snapshot`: 临时库插多行,断言返回 `时间` 最大的行;空库 -> `None`。
- `forecast_at`: 临时 CSV,断言精确 `(时间,线路)` 命中;缺失/超范围 -> `0`。
- `build_firstline_msg`/`build_secondline_msg`: 喂固定 fixture dict,断言整串 markdown(snapshot,覆盖字段映射 + 派生指标 流入率/接通率/接通量=转人工量-转人工失败)。
- `check_alerts` 阈值: mock `_send_text` 记录调用;断言 热线排队=10(等于阈值,告警)/=9(不告警),且 空闲<排队(告警)/空闲≥排队(不告警);在线排队=20(等于,告警)/=19(不告警);12378 同理(=1 告警/=0 不告警)。
- `check_alerts` 窗口: 注入 `now`(周末 18:30),断言 12378 即便 latest 排队超阈值也不告警(其窗口已关);同 `now` 下热线/在线仍正常判。
- 不发真实 webhook/截图(全 mock);真实 webhook 联通性留作手动冒烟。

## 10. 已确认的澄清决策

1. 在线告警: 仅 `排队>20`,不加空闲条件(在线无"空闲"指标)。
2. 告警频率: 每采集周期(5 分钟)条件成立就发,无去重(沿用 v14)。
3. 工单量: 常规=工单明细.回访组一组, 贷后=工单明细.贷后回访组(spec §4.1)。
4. 推送时段: 仅全局窗口(9:00-21:00)内推送。

## 11. 现有 stub 替换说明

`collector/notify.py` 现有 `send_alert(message, **ctx)` / `take_screenshot(url=None)` 两个 stub 被 `check_alerts(cfg)` / `send_report(cfg)` + 内部 `_webhook`/`_send_*`/`take_screenshot` 取代。`scheduler.py` 中 `detail_job` 末尾注释 `# notify.send_alert(...); notify.take_screenshot(...)` 改为在 `ws_job` 调 `notify.check_alerts(cfg)`(告警基于 WS 数据,非 detail)。
