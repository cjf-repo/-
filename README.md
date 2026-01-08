# Multi-hop Multipath Proxy Prototype

This project is a lightweight, real-communication prototype of a multi-hop, multi-path proxy chain:

```
ClientApp -> Entry -> Middle_i -> Exit -> Server (echo)
```

Return path:

```
Server -> Exit -> Middle_i -> Entry -> ClientApp
```

## Features

- Multi-hop, multi-path tunneling with weighted batching.
- Behavior shaping (size bins, padding budget, jitter).
- Proto/attribute obfuscation with 3 rotating protocol profiles.
- Simple ACK-based RTT/loss estimation.
- Window-based strategy updates.

## Layout

- `config.py` - central configuration.
- `frames.py` - binary frame format and fragment buffer.
- `profiles.py` - protocol profile templates and handshake patterns.
- `obfuscation.py` - proto obfuscator (profile selection + header padding).
- `behavior.py` - behavior shaping (size bins, padding budget, jitter).
- `scheduler.py` - multi-path scheduler.
- `strategy.py` - window strategy engine.
- `nodes/` - runnable asyncio services.
- `start_all.py` - one-shot launcher.

## Quick start

```bash
python start_all.py
```

This launches:

- `nodes/server.py` on `127.0.0.1:9301`
- `nodes/exit.py` on `127.0.0.1:9201`
- two `nodes/middle.py` instances on `127.0.0.1:9101`, `9102`
- `nodes/entry.py` on `127.0.0.1:9001`
- `nodes/client_app.py` which sends random bytes and verifies echo.

## Config

All parameters are centralized in `config.py`:

- Ports for entry/middle/exit/server
- `window_size_sec`
- `size_bins`, `padding_alpha`, `jitter_ms`
- `batch_size`, `redundancy`

## Notes

- The frame header is a fixed binary struct with an extra header length field for proto obfuscation.
- ACK frames carry the acknowledged `seq` in payload.
- This is a prototype, designed for extension (e.g. adding pcap/log sinks).
