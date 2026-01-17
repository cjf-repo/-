from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
import uuid
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, Future
from typing import Iterable
from urllib.parse import urlparse
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
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="curl 超时秒数（<=0 表示不设置超时）",
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="每次访问间隔秒数")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="并发请求数量（默认 1，>1 会并发执行 URL 访问）",
    )
    parser.add_argument(
        "--max-pending",
        type=int,
        default=0,
        help="待执行任务上限（0 表示自动 = 并发数 * 4）",
    )
    parser.add_argument(
        "--user-agent",
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        help="curl User-Agent（默认模拟浏览器）",
    )
    parser.add_argument(
        "--follow-redirects",
        action="store_true",
        default=True,
        help="跟随 301/302 重定向（默认启用）",
    )
    parser.add_argument(
        "--no-follow-redirects",
        action="store_false",
        dest="follow_redirects",
        help="关闭重定向跟随",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="允许不安全的 HTTPS 证书（等价于 curl -k）",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="保存响应内容的目录（默认不保存）",
    )
    parser.add_argument(
        "--pcap-dir",
        default=None,
        help="每次请求单独抓包时的输出目录（默认不单独抓包）",
    )
    parser.add_argument(
        "--pcap-wait",
        type=float,
        default=0.2,
        help="启动抓包后等待秒数，避免丢首包",
    )
    parser.add_argument(
        "--pcap-serial",
        action="store_true",
        default=True,
        help="抓包时串行执行请求（默认启用，避免并发抓包导致重复流量统计）",
    )
    parser.add_argument(
        "--pcap-parallel",
        action="store_false",
        dest="pcap_serial",
        help="允许抓包并发执行（可能导致多份 pcap 记录同一流量）",
    )
    parser.add_argument(
        "--failures-file",
        default=None,
        help="保存失败请求列表的文件路径（默认不保存）",
    )
    parser.add_argument(
        "--print-cmd",
        action="store_true",
        help="打印每次执行的 curl 命令",
    )
    parser.add_argument(
        "--cmd-file",
        default=None,
        help="保存每次执行的 curl 命令到文件（默认不保存）",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=0,
        help="失败后重试次数（默认不重试）",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=0.5,
        help="失败重试间隔秒数",
    )
    return parser.parse_args()


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        urls.append(url)
    return urls


def build_curl_cmd(
    url: str,
    proxy: str,
    timeout: int,
    *,
    user_agent: str,
    follow_redirects: bool,
    insecure: bool,
    output_path: Path | None,
) -> list[str]:
    cmd = [
        "curl",
        "-sS",
        "--proxy",
        proxy,
        "-A",
        user_agent,
        url,
    ]
    if timeout > 0:
        cmd.insert(4, "--max-time")
        cmd.insert(5, str(timeout))
    if follow_redirects:
        cmd.insert(1, "-L")
    if insecure:
        cmd.insert(1, "-k")
    if output_path is None:
        cmd.extend(["-o", "/dev/null"])
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-o", str(output_path)])
    return cmd


def run_curl(cmd: list[str]) -> int:
    return subprocess.call(cmd)


def start_capture(out_dir: Path) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "tools.capture_pcap",
        "--out-dir",
        str(out_dir),
    ]
    return subprocess.Popen(cmd)


def url_label(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.netloc or parsed.path
    safe = []
    for ch in host:
        safe.append(ch if ch.isalnum() else "_")
    return "".join(safe).strip("_") or "url"


def move_pcap_files(run_dir: Path, base_dir: Path, label: str, count: int) -> None:
    target_dir = base_dir / label
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in run_dir.glob("*.pcap"):
        target = target_dir / f"{count}_{path.stem}.pcap"
        path.replace(target)
    run_dir.rmdir()


def main() -> None:
    args = parse_args()
    urls = read_urls(Path(args.input))
    if not urls:
        raise SystemExit("未读取到有效 URL，请检查输入文件。")
    if args.times < 1:
        raise SystemExit("--times 必须 >= 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency 必须 >= 1")
    if args.retry < 0:
        raise SystemExit("--retry 必须 >= 0")
    if args.max_pending < 0:
        raise SystemExit("--max-pending 必须 >= 0")
    max_pending = args.max_pending or args.concurrency * 4
    if max_pending < args.concurrency:
        max_pending = args.concurrency
    failures = 0
    failure_entries: list[str] = []
    cmd_lines: list[str] = []
    cmd_lock = threading.Lock()
    pcap_lock = threading.Lock()
    def iter_tasks() -> Iterable[tuple[str, int]]:
        for url in urls:
            for count in range(1, args.times + 1):
                yield url, count

    def worker(url: str, count: int) -> int:
        capture_proc = None
        run_dir = None
        output_path = None
        pcap_guard: contextlib.AbstractContextManager = contextlib.nullcontext()
        if args.pcap_dir is not None and args.pcap_serial:
            pcap_guard = pcap_lock
        with pcap_guard:
            try:
                if args.pcap_dir is not None:
                    run_id = uuid.uuid4().hex[:8]
                    run_dir = Path(args.pcap_dir) / run_id
                    capture_proc = start_capture(run_dir)
                    if args.pcap_wait > 0:
                        time.sleep(args.pcap_wait)
                if args.output_dir is not None:
                    output_path = Path(args.output_dir) / f"{url_label(url)}_{count}.html"
                cmd = build_curl_cmd(
                    url,
                    args.proxy,
                    args.timeout,
                    user_agent=args.user_agent,
                    follow_redirects=args.follow_redirects,
                    insecure=args.insecure,
                    output_path=output_path,
                )
                if args.print_cmd or args.cmd_file is not None:
                    cmd_line = " ".join(cmd)
                    with cmd_lock:
                        cmd_lines.append(cmd_line)
                        if args.print_cmd:
                            print(cmd_line, flush=True)
                attempts = args.retry + 1
                for attempt in range(attempts):
                    code = run_curl(cmd)
                    if code == 0:
                        return code
                    if attempt < attempts - 1 and args.retry_delay > 0:
                        time.sleep(args.retry_delay)
                return code
            finally:
                if capture_proc is not None:
                    capture_proc.send_signal(signal.SIGINT)
                    try:
                        capture_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        capture_proc.terminate()
                        capture_proc.wait(timeout=5)
                if run_dir is not None and args.pcap_dir is not None:
                    label = url_label(url)
                    move_pcap_files(run_dir, Path(args.pcap_dir), label, count)
                if args.sleep > 0:
                    time.sleep(args.sleep)

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        task_iter = iter_tasks()
        pending: dict[Future[int], tuple[str, int]] = {}

        def submit_next() -> bool:
            try:
                next_task = next(task_iter)
            except StopIteration:
                return False
            future = executor.submit(worker, *next_task)
            pending[future] = next_task
            return True

        while len(pending) < max_pending and submit_next():
            continue

        while pending:
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                url, count = pending.pop(future)
                code = future.result()
                if code != 0:
                    failures += 1
                    failure_entries.append(f"{url}\t{count}")
                while len(pending) < max_pending and submit_next():
                    continue
    if failures:
        if args.failures_file is not None:
            failure_path = Path(args.failures_file)
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text("\n".join(failure_entries), encoding="utf-8")
        raise SystemExit(f"共有 {failures} 次请求失败。")
    if args.cmd_file is not None and cmd_lines:
        cmd_path = Path(args.cmd_file)
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
        cmd_path.write_text("\n".join(cmd_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
