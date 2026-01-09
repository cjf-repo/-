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
