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

## 实验输出目录与复现信息

每次运行会生成一个 `run_id`，并输出到：

```
out/<run_id>/
```

目录包含：

- `config_dump.json`：本次运行配置快照
- `meta.json`：run_id、seed、attacker_path_id、启动时间
- `window_logs.jsonl`：窗口级结构化日志
- `traces/`：攻击者视角 TM1/TM2 CSV

### baseline 对照模式

在 `config.py` 里通过 `mode` 选择运行模式：

- `normal`：完整功能（多路径 + 行为伪装 + 属性伪装）
- `baseline_delay`：只开启 pacing/jitter（禁用整形/填充/协议变体，路径数=1）
- `baseline_padding`：只开启整形+填充（禁用 pacing/jitter/协议变体，路径数=1）

## 配置说明

所有参数集中在 `config.py`：

- 端口与节点拓扑（entry/middle/exit/server）
- `window_size_sec`
- `size_bins`, `padding_alpha`, `jitter_ms`
- `batch_size`, `redundancy`

## 备注

- 帧头为固定结构，并包含“额外头字段长度”以便协议伪装。
- ACK 帧在 payload 中携带确认的 `seq`。
- 本原型便于后续扩展（如抓包/日志、SOCKS/TUN 入口等）。
