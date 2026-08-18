# 生产业务数据字典（Production Data Dictionary）

> 本文件是 Data Agent 分析生产瓶颈时的**业务口径权威来源**。
> 业务人员可随时修改，agent 通过 `get_production_dictionary` 工具按需读取。
> 修改后无需改代码，重启服务（或新会话）即生效。

## 1. 业务概览

数据库 `agile_dispatch` 是 AGV 柔性产线调度系统，核心实体：

- **设备（vehicle）**：AGV/AMR 小车（如 `AMR-CS-01`、`A03`），在产线中搬运物料、上下料
- **CNC 机床（device）**：加工工位（如 `A03`），任务的目标设备
- **任务（task）**：一次上下料/搬运动作，由调度系统下发

三张分析用表：
| 表 | 粒度 | 用途 |
|---|---|---|
| `t_device_statistical_metrics` | 设备状态事件明细 | 某设备某状态的时间段（秒） |
| `t_device_statistical_metrics_agg` | 聚合统计 | 按小时/天聚合成时段内的指标 |
| `t_prod_reports` | 任务报表（行=一次任务） | 任务耗时、动作、结束状态 |

---

## 2. t_device_statistical_metrics —— 设备状态明细

记录设备（CNC/AGV）各状态发生的**时间段**。每个事件一行：`start_at`~`end_at` 处于某 `metrics_name` 状态，`duration` 为该段持续秒数。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | uuid | 主键 |
| `start_at` / `end_at` | timestamp | 状态开始 / 结束时间（`end_at` 为 NULL 表示状态持续中） |
| `device_name` | varchar | 设备名（CNC 机床或 AGV） |
| `metrics_name` | varchar | 状态名（见枚举） |
| `metrics_type` | varchar | 状态类型：`cnc_state` / `vehicle_alarm` / `vehicle_state` |
| `duration` | double | 该状态持续秒数 |

### 枚举：metrics_type × metrics_name

| metrics_type | metrics_name | 含义 | 增值/浪费 |
|---|---|---|---|
| `cnc_state` | `加工时间` | 机床实际加工 | ✅ 增值时间 |
| `cnc_state` | `待料时间` | 机床等物料 | ⚠️ 浪费（缺料等待） |
| `cnc_state` | `提前呼叫时间` | 提前呼叫 AGV 的窗口 | 中性（调度提前量） |
| `cnc_state` | `手动模式时间` | 人工介入 | ⚠️ 浪费/干预 |
| `vehicle_state` | `待机时间` | AGV 空闲等任务 | ⚠️ 浪费（设备闲置） |
| `vehicle_state` | `离线时间` | AGV 离线/掉线 | ⚠️ 异常 |
| `vehicle_state` | `手动模式时间` | 人工操作 AGV | ⚠️ 干预 |
| `vehicle_alarm` | `与调度通讯中断` | 与调度断连告警 | ⚠️ 异常 |

**分析提示**：瓶颈通常集中在**高占比的"浪费"状态**（待料、待机、离线）和**异常事件**（通讯中断）。
注意 `duration` 可能为 0（瞬时事件或未累积），分析时优先用聚合表。

---

## 3. t_device_statistical_metrics_agg —— 聚合统计

按 `time_window`（小时或天）把 `t_device_statistical_metrics` 聚合成时段指标，便于看趋势、跨设备对比。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | uuid | 主键 |
| `device_name` | varchar | 设备名 |
| `metrics_name` | varchar | 状态名（同明细表） |
| `metrics_type` | varchar | 状态类型 |
| `time_window` | timestamp | 窗口起始时间 |
| `window_type` | varchar | 窗口粒度：`hour` / `day` |
| `metrics_ids` | jsonb | 明细行 id 列表 |
| `metrics_count` | int | 该窗口内事件次数 |
| `total_duration` | double | 该窗口内累计秒数（**核心指标**） |
| `time_segments` | jsonb | 明细时间段数组 |

**分析提示**：
- 用 `window_type='hour'` 看**日内峰值**（瓶颈时段），`window_type='day'` 看**趋势**
- 对比设备：`WHERE device_name=...` 单设备诊断；`GROUP BY device_name` 多设备对比
- 同一窗口同一设备可能有多行不同 `metrics_name`，聚合时注意分组

---

## 4. t_prod_reports —— 任务报表

**一行 = 一次 AGV 任务**，记录任务全程耗时分解与结束状态，是瓶颈分析的主表。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | uuid | 主键 |
| `task_id` | varchar | 任务编号 |
| `device_name` | varchar | 执行任务的 AGV |
| `target_device_name` | varchar | 目标设备（如 `AQ23-AQ24` 工位） |
| `task_start_time` / `task_end_time` | timestamp | 任务起止 |
| `task_used` | double | **任务总耗时（秒）** |
| `move_used` | double | 移动（导航行走）耗时 |
| `cv_used` | double | 视觉识别耗时（功能未启用，通常为 0） |
| `plc_config_used` | double | PLC 配置耗时 |
| `plc_wait_finished_used` | double | **PLC 等待完成耗时（强等待指标）** |
| `action_used` | double | 动作执行耗时（上下料） |
| `action_type` | varchar | 动作类型（AGV 在某工位上下料） |
| `abnormal_info` | varchar | 异常描述（见枚举） |
| `normal_end` | int | 任务结束状态（**见下，最关键枚举**） |
| `raw` | jsonb | 原始报文（含坐标等，一般不用） |

### 枚举：normal_end（任务结束状态）—— 最关键

| 值 | 含义 | 是否瓶颈信号 |
|---|---|---|
| `1` | **正常完成** | ✅ 正常 |
| `0` | **异常结束**（任务失败） | 🔴 强瓶颈 |
| `3` | **异常告警完成**（执行中曾告警，但任务最终完成） | 🟠 潜在问题 |
| `2` | **切换目的地**（当前任务取消，转去执行新任务） | 🟡 调度变动 |

> 统计"任务成功率"时：正常完成 = `normal_end=1`；
> 硬失败 = `normal_end=0`；把 `2`、`3` 单独统计，不应与 `1` 混淆。
> **`normal_end=2` 常与 `abnormal_info` 含"切换目的地"同时出现。**

### 枚举：abnormal_info（异常信息，常见值）

| 值 | 含义 | 提示 |
|---|---|---|
| `无异常` | 无异常 | 正常 |
| `导航失败` | AGV 导航/路径规划失败 | 🔴 系统级瓶颈候选，优先排查 |
| `切换目的地` | 任务被调度切换目标 | 对应 normal_end=2 |
| `与调度通讯中断` | 车辆与调度断连 | 通讯问题 |
| `任务取消` | 任务被取消 | — |
| `异常结束` | 异常终止 | 对应 normal_end=0 |

---

## 5. 生产瓶颈分析口径（分析时的统一方法）

### 5.1 三视角框架
1. **任务结果视角**（t_prod_reports）：异常率、失败归因、耗时结构
2. **设备状态视角**（t_device_statistical_metrics / _agg）：浪费时间占比、离线/待料
3. **时间趋势视角**（_agg 的 hour/day 窗口）：瓶颈时段、趋势恶化

### 5.2 关键指标
- **异常率** = `count(normal_end != 1) / count(*)`（越高越堵）
- **硬失败率** = `count(normal_end = 0) / count(*)`
- **导航失败次数 / 占比**（abnormal_info='导航失败'）—— 优先排查
- **任务耗时结构**：`plc_wait_finished_used / task_used`、`move_used / task_used`、
  `action_used / task_used` 占比，定位时间花在哪
- **设备等待占比**：某设备 `待料时间`、`待机时间`、`离线时间` 占总统计时长比例
- **瓶颈时段**：agg 表按 hour 分组，`total_duration` 峰值时段

### 5.3 分析建议顺序
1. 先看 `t_prod_reports` 的整体异常率与失败归因（GROUP BY abnormal_info, normal_end）
2. 拆时间结构，看哪项耗时占主导（如 plc_wait_finished_used 长 → 等待瓶颈）
3. 用 _agg 表定位瓶颈设备 / 瓶颈时段（hour 粒度）
4. 下钻到单设备 / 单任务明细确认根因
5. 可视化：bar（设备/异常对比）、pie（耗时结构、异常构成）、histogram（耗时分布）

### 5.4 口径注意
- `cv_used` 功能未启用（通常为 0），不要把它当瓶颈
- `duration` / `total_duration` 单位为**秒**；任务耗时单位也是**秒**
- `_agg` 表行数很大（聚合结果），查询务必 `WHERE` 过滤时间窗和设备，避免全表扫
- `normal_end` 勿简单二分（非 1 即坏）——`2`(切换)与`3`(告警完成)需单独解读

---

## 6. 时间范围与默认分析口径

### 6.1 数据新鲜度（会随时间变化，以 get_current_date 工具返回为准）

| 表 | 时间列 | 说明 |
|---|---|---|
| `t_prod_reports` | `task_start_time` | **可能滞后**（滞后程度取决于采集窗口） |
| `t_device_statistical_metrics` | `start_at` | 可能滞后 |
| `t_device_statistical_metrics_agg` | `time_window` | 接近实时（小时粒度） |

> 三张表数据新鲜度不同：聚合表通常最新，任务表/明细表可能滞后。
> **分析前务必先用 get_current_date() 确认各表最新数据日期，不要假设数据覆盖到当天。**

### 6.2 默认时间段规则
- 用户**未指定时间段** → 默认分析**当日**（get_current_date 的 today）。
- 用户**指定了时间段** → 按其指定，不使用默认。
- 当日无数据（如任务表滞后）→ 如实说明"当日无数据，最新到 X"，并主动用最新日期分析或询问。

### 6.3 SQL 时间过滤写法
- **当日**（半开区间，避免漏掉当天最后一条）：
  `WHERE task_start_time >= 'YYYY-MM-DD 00:00:00' AND task_start_time < 'YYYY-MM-DD+1 00:00:00'`
- **日期范围**：
  `WHERE task_start_time >= '起' AND task_start_time < '止+1天'`
- **最近 N 天**（用当前日期往前推）：
  `WHERE task_start_time >= current_date - interval 'N days'`
- 聚合表直接过滤 `time_window`（timestamp 类型），同样用半开区间。

---

## 7. 产量与 AMR 送料口径（核心分析维度）

> **产量没有直接的计数表，用「AMR 送料次数」近似。**
> **人工换料（AMR 人工换料）也计入产量。**

### 7.1 送料任务识别（SQL 口径）

**送料任务（产量）** = `device_name` 是 AMR（`LIKE 'AMR%'`）**且** `action_type` 含"上下料"**或"人工换料"**：

```sql
SELECT date(task_start_time) AS d, count(*) AS feed_count
FROM t_prod_reports
WHERE device_name LIKE 'AMR%'
  AND (action_type LIKE '%上下料%' OR action_type LIKE '%人工换料%')
GROUP BY d ORDER BY d;
```

**送料次数 ≈ 产量**。主要送料动作类型示例：
- `【AMR】给【2-1夹区】【精雕】【上下料】`（给精雕机上下料）
- `AMR给一拖二上下料`
- `AMR人工换料`（人工参与的换料，**计入产量**）

**常见变体**：
- 只看"给精雕送料"：`action_type = '【AMR】给【2-1夹区】【精雕】【上下料】'`
- 单独统计人工换料：`AND action_type LIKE '%人工换料%'`
- 只统计"正常完成"的送料：`AND normal_end = 1`

### 7.2 送料次数"多/少"的归因分析框架

送料次数波动（多/少）应结合以下维度解释，不要只看数字：

**次数少（产量低）的原因候选**：
1. **异常高发**：`normal_end != 1` 占比高 → 按 `abnormal_info` 归因
   （注意：送料次数高不等于正常完成多——异常任务数可能同步升高，需分开统计）
2. **设备离线 / 通讯中断**：`vehicle_state` 离线、`vehicle_alarm` 通讯中断
3. **任务取消 / 切换目的地**：`normal_end=0/2`、`abnormal_info` 含"切换目的地"
4. **数据缺失 / 采集滞后**：任务表可能滞后，送料骤减先确认是否数据未采集完整
   （用 get_current_date() 核对各表最新数据日期）
5. **待料 / 待机**：`t_device_statistical_metrics` 的"待料时间""待机时间"占比高

**次数多（产量高）的原因候选**：
1. **多设备并行**：参与送料的 `DISTINCT device_name` 数量多
2. **加班 / 长时段生产**：按 `date_part('hour', task_start_time)` 看时段分布是否覆盖更多小时
3. **调度顺畅**：异常率低、正常完成占比高
4. **节拍快**：平均 `task_used` 短（对比其他天）

**分析建议顺序**：
1. 按天（或用户指定粒度）统计送料次数 → 找**异常高/低点**（可用 z-score 或环比）
2. 对极值天**下钻**：异常归因、设备参与数、时段分布
3. 结合设备状态表（_agg）看当天待料/待机/离线情况
4. 可视化：line/bar（送料次数趋势）、pie（异常构成）、bar（设备贡献）
5. 结论给出：送料次数少 → 主要归因是异常/设备/数据之一；并量化占比
