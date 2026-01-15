from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量读取 URL 列表并通过 curl 依次访问。"
    )
    parser.add_argument("--input", required=True, help="包含 URL 的 txt 文件路径")
    parser.add_argument("--times", type=int, default=1, help="每个 URL 访问次数")
    parser.add_argument(
        "--proxy",
        default="http://127.0.0.1:9001",
        help="代理地址（入口节点），例如 http://127.0.0.1:9001",
    )
    parser.add_argument("--timeout", type=int, default=20, help="curl 超时秒数")
    parser.add_argument("--sleep", type=float, default=0.0, help="每次访问间隔秒数")
    return parser.parse_args()


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        urls.append(url)
    return urls


def run_curl(url: str, proxy: str, timeout: int) -> int:
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "--proxy",
        proxy,
        "--max-time",
        str(timeout),
        url,
    ]
    return subprocess.call(cmd)


def main() -> None:
    args = parse_args()
    urls = read_urls(Path(args.input))
    if not urls:
        raise SystemExit("未读取到有效 URL，请检查输入文件。")
    if args.times < 1:
        raise SystemExit("--times 必须 >= 1")
    failures = 0
    for url in urls:
        for _ in range(args.times):
            code = run_curl(url, args.proxy, args.timeout)
            if code != 0:
                failures += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
    if failures:
        raise SystemExit(f"共有 {failures} 次请求失败。")


if __name__ == "__main__":
    main()
