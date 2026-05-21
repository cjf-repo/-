#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
configs_dir="$root_dir/configs/instances"

if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3，无法解析配置文件。" >&2
  exit 1
fi

ports="$(python3 - <<'PY'
import json
from pathlib import Path

root = Path("configs/instances")
ports = set()
for path in sorted(root.glob("instance*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("entry_port", "exit_port", "server_port"):
        value = data.get(key)
        if value is not None:
            ports.add(int(value))
    for port in data.get("middle_ports", []):
        ports.add(int(port))
    raw_paths = data.get("paths")
    if isinstance(raw_paths, list):
        for raw_route in raw_paths:
            if isinstance(raw_route, list):
                raw_hops = raw_route
            elif isinstance(raw_route, dict):
                raw_hops = raw_route.get("hops")
            else:
                continue
            if not isinstance(raw_hops, list):
                continue
            for raw_hop in raw_hops:
                if isinstance(raw_hop, int):
                    ports.add(int(raw_hop))
                elif isinstance(raw_hop, dict):
                    port = raw_hop.get("listen_port", raw_hop.get("port"))
                    if port is not None:
                        ports.add(int(port))
print(" ".join(str(p) for p in sorted(ports)))
PY
)"

if [[ -z "${ports}" ]]; then
  echo "未找到可清理的端口。" >&2
  exit 0
fi

echo "准备清理端口: ${ports}"

if command -v fuser >/dev/null 2>&1; then
  # fuser 可能会返回非零（无进程占用），这里忽略错误。
  fuser -k -n tcp ${ports} >/dev/null 2>&1 || true
  exit 0
fi

if command -v lsof >/dev/null 2>&1; then
  for port in ${ports}; do
    pids="$(lsof -ti tcp:${port} || true)"
    if [[ -n "${pids}" ]]; then
      kill ${pids} >/dev/null 2>&1 || true
    fi
  done
  exit 0
fi

echo "未找到 fuser 或 lsof，无法自动清理端口占用。" >&2
exit 1
