# 多跳多路径代理原型

本项目是一个可真实通信的轻量多跳多路径代理原型，链路结构如下：

```
ClientApp -> Entry -> Middle_i -> Exit -> Server (echo)
```

回程方向：

```
Server -> Exit -> Middle_i -> Entry -> ClientApp
```

## 功能特性

- 多跳、多路径隧道，带加权批量调度。
- 行为整形（包尺寸分桶、填充预算、抖动）。
- 属性伪装（3种协议模板可轮换）。
- 基于 ACK 的 RTT/丢包估计。
- 按时间窗动态更新策略。

## 目录结构

- `config.py`：集中配置。
- `frames.py`：二进制帧格式与分片重组。
- `profiles.py`：协议模板与握手模式。
- `obfuscation.py`：协议伪装器（模板选择 + 头部变体）。
- `behavior.py`：行为整形（分桶、填充预算、抖动）。
- `scheduler.py`：多路径调度器。
- `strategy.py`：时间窗策略引擎。
- `nodes/`：各节点服务。
- `run_context.py`：运行上下文（输出目录、日志与配置快照）。
- `metrics.py`：离线指标汇总（feature/result JSON）。
- `run_experiments.py`：批量 sweep 实验脚本。
- `start_all.py`：一键启动脚本。

## 快速启动

```bash
python start_all.py
```

该命令会依次启动：

- `nodes/server.py`：`127.0.0.1:9301`
- `nodes/exit.py`：`127.0.0.1:9201`
- `nodes/middle.py`：`127.0.0.1:9101`, `9102`
- `nodes/entry.py`：`127.0.0.1:9001`
- `nodes/client_app.py`：发送随机数据并校验回显

## 运行参数与环境变量

本项目支持通过环境变量覆盖实验参数，便于批量扫参：

- `PATH_COUNT`：路径数（通过裁剪 `middle_ports` 列表实现）
- `OBFUSCATION_LEVEL`：混淆等级（0~3）
- `ALPHA_PADDING`：padding 系数
- `MODE`：运行模式（`normal` / `baseline_delay` / `baseline_padding`）
- `PROTO_SWITCH_PERIOD`：协议族切换周期（窗口数）
- `ADAPTIVE_PATHS` / `ADAPTIVE_BEHAVIOR` / `ADAPTIVE_PROTO`：自适应消融开关
- `SEED`：随机种子
- `RUN_ID` / `OUT_DIR`：输出目录控制
- `SESSION_COUNT` / `SESSION_DURATION`：客户端发送次数/时长

示例（固定 2 条路径、关闭行为自适应、协议按 3 个窗口切换）：

```bash
PATH_COUNT=2 OBFUSCATION_LEVEL=2 ALPHA_PADDING=0.05 \
ADAPTIVE_PATHS=1 ADAPTIVE_BEHAVIOR=0 ADAPTIVE_PROTO=1 \
PROTO_SWITCH_PERIOD=3 python start_all.py
```

## 实验输出目录与复现信息

每次运行会生成一个 `run_id`，并输出到：

```
out/<run_id>/
```

目录包含：

- `config_dump.json`：本次运行配置快照
- `meta.json`：run_id、seed、attacker_path_id、启动时间
- `window_logs.jsonl`：窗口级结构化日志（策略、权重、协议、行为参数）
- `latency_logs.jsonl`：逐条消息延迟记录
- `traces/`：路径与攻击者视角 TM1/TM2 CSV
- `feature_summary.json`：特征统计（由 metrics 生成）
- `results_summary.json`：结果统计（由 metrics 生成）

## 离线指标汇总

单次运行结束后可用 `metrics.py` 生成统计结果：

```bash
python metrics.py --run-dir out/<run_id>
```

输出：

- `feature_summary.json`：TM1/TM2 包长/IAT/burst 统计与路径相关性
- `results_summary.json`：success_rate、P50/P95 latency、amplification、padding_ratio

## 批量实验（sweep）

运行批量 sweep 脚本（会自动调用 `start_all.py` + `metrics.py`）：

```bash
python run_experiments.py
```

默认 sweep 参数：

- `PATH_COUNT`：2/3/4
- `OBFUSCATION_LEVEL`：0/1/2/3
- `ALPHA_PADDING`：0.02/0.05/0.1
- `PROTO_SWITCH_PERIOD`：1/3/5
- `ADAPTIVE_*`：static / adaptive_paths_only / adaptive_behavior_only / adaptive_proto_only / full_adaptive
- baseline：`baseline_delay` / `baseline_padding`（固定单路径）

可直接修改 `run_experiments.py` 中的参数列表来裁剪实验规模。

## 实验步骤建议（论文复现实验）

1) 选择实验目标（对比自适应开关 / 不同混淆等级 / 不同 padding 系数）。
2) 设定运行参数（环境变量或直接修改 `run_experiments.py`）。
3) 执行批量 sweep 或单次运行。
4) 对每个 run 的 `feature_summary.json` 与 `results_summary.json` 做统计汇总。
5) 如需可视化或分类，使用 `traces/` 中 TM1/TM2 CSV 做后续分析。

## baseline 对照模式

在 `config.py` 里通过 `mode` 选择运行模式：

- `normal`：完整功能（多路径 + 行为伪装 + 属性伪装）
- `baseline_delay`：只开启 pacing/jitter（禁用整形/填充/协议变体，路径数=1）
- `baseline_padding`：只开启整形+填充（禁用 pacing/jitter/协议变体，路径数=1）

## 输出字段简要说明

- `window_logs.jsonl`：每条记录包含 `window_id/path_id`、策略参数、`proto_family/variant`、
  `padding_bytes/real_bytes`、RTT/丢包、以及自适应触发信息。
- `latency_logs.jsonl`：逐条消息的 `latency_ms` 与成功标记。
- `traces/`：`trace_session_*_path_*_TM1.csv` 与 `trace_session_*_path_*_TM2.csv`。

## 监控与验证（实时/双路）

本项目提供独立的实时监控代理，用于观察隧道帧的 `proto_id`、`flags`、`extra` 等字段，
无需修改节点逻辑。

### 一键带监控启动（单路）

```bash
python start_with_monitor.py
```

该脚本会在第一条路径前插入实时监控代理（默认 9103 → 9101），并启动全链路与 client。

### 一键带监控启动（双路对比）

```bash
python start_with_dual_monitor.py
```

该脚本会在两条路径前分别插入监控代理（默认 9103 → 9101，9104 → 9102），
便于对比 `path=0` 与 `path=1` 的协议外观与行为特征。

### 手动插入监控代理

1) 启动监控代理（独立进程）：

```bash
python -m tools.monitor_live --listen-port 9103 --target-port 9101
```

2) 让入口连接监控端口（通过参数覆盖）：

```bash
python -m nodes.entry --middle-ports 9103,9102
```

### 离线 pcap 解析

```bash
python -m tools.pcap_reader --pcap path/to/file.pcap --port 9101 --port 9102
```

## 关键字段解释

实时监控输出中常见字段含义：

- `proto`：协议模板编号（用于属性伪装对比）
- `flags`：帧标记（握手/分片/填充/ACK）
- `extra`：额外头长度（不同模板范围不同）
- `frag=a/b`：分片编号与分片总数

通过比较 `path=0` 与 `path=1` 的 `proto/extra/flags/frag` 分布，可以观察两条路径在
协议外观与行为特征上的差异。

## 配置说明

所有参数集中在 `config.py`：

- 端口与节点拓扑（entry/middle/exit/server）
- `window_size_sec`
- `size_bins`, `padding_alpha`, `jitter_ms`
- `batch_size`, `redundancy`
- `proto_switch_period`
- `adaptive_paths / adaptive_behavior / adaptive_proto`

## 备注

- 帧头为固定结构，并包含“额外头字段长度”以便协议伪装。
- ACK 帧在 payload 中携带确认的 `seq`。
- 本原型便于后续扩展（如抓包/日志、SOCKS/TUN 入口等）。
