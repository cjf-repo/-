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

from config import DEFAULT_CONFIG

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
        "--proxy-ports",
        default=None,
        help="多个入口端口（逗号/范围），如 9001,9002 或 9001-9006；"
        "配合 --proxy 的主机与协议生成多个代理地址；"
        "未提供则使用 BATCH_PROXY_PORTS 配置",
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
        "--pcap-follow-proxy-ports",
        action="store_true",
        default=True,
        help="抓包端口随入口端口偏移（默认启用）",
    )
    parser.add_argument(
        "--no-pcap-follow-proxy-ports",
        action="store_false",
        dest="pcap_follow_proxy_ports",
        help="抓包端口不随入口端口偏移",
    )
    parser.add_argument(
        "--pcap-entry-port-base",
        type=int,
        default=9001,
        help="抓包入口端口基准（用于计算端口偏移）",
    )
    parser.add_argument(
        "--pcap-exit-port-base",
        type=int,
        default=9201,
        help="抓包出口端口基准（用于计算端口偏移）",
    )
    parser.add_argument(
        "--pcap-middle-ports-base",
        default="9101,9102",
        help="抓包中继端口基准列表（用于计算端口偏移）",
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


def start_capture(
    out_dir: Path,
    *,
    entry_port: int,
    exit_port: int,
    middle_ports: list[int],
) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "tools.capture_pcap",
        "--out-dir",
        str(out_dir),
        "--entry-port",
        str(entry_port),
        "--exit-port",
        str(exit_port),
        "--middle-ports",
        ",".join(str(port) for port in middle_ports),
    ]
    return subprocess.Popen(cmd)


def url_label(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.netloc or parsed.path
    safe = []
    for ch in host:
        safe.append(ch if ch.isalnum() else "_")
    return "".join(safe).strip("_") or "url"


def parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if start > end:
                start, end = end, start
            ports.extend(range(start, end + 1))
        else:
            ports.append(int(part))
    return ports


def build_proxy_list(proxy: str, proxy_ports: str | None) -> list[str]:
    if proxy_ports is None:
        return [proxy]
    parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    scheme = parsed.scheme or "http"
    host = parsed.hostname or parsed.path
    if not host:
        raise SystemExit("--proxy 解析失败，请提供包含主机的地址。")
    ports = parse_ports(proxy_ports)
    if not ports:
        raise SystemExit("--proxy-ports 未解析到有效端口。")
    return [f"{scheme}://{host}:{port}" for port in ports]


def move_pcap_files(run_dir: Path, base_dir: Path, label: str, count: int) -> None:
    target_dir = base_dir / label
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in run_dir.glob("*.pcap"):
        target = target_dir / f"{count}_{path.stem}.pcap"
        path.replace(target)
    run_dir.rmdir()


def main() -> None:
    args = parse_args()
    if args.proxy_ports is None and DEFAULT_CONFIG.batch_proxy_ports:
        args.proxy_ports = DEFAULT_CONFIG.batch_proxy_ports
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
    proxies = build_proxy_list(args.proxy, args.proxy_ports)
    if args.pcap_dir is not None and args.pcap_serial and args.concurrency > 1 and len(proxies) < 2:
        raise SystemExit(
            "抓包串行模式下并行实验需要多个入口端口，请使用 --proxy-ports 或配置 "
            "BATCH_PROXY_PORTS 指定不同端口。"
        )
    effective_concurrency = args.concurrency
    if args.pcap_dir is not None and args.pcap_serial and len(proxies) < args.concurrency:
        print(
            f"抓包串行模式下入口端口数量为 {len(proxies)}，"
            f"实际吞吐受限于端口数量（请求会在端口内串行执行），"
            f"当前并发参数为 {args.concurrency}。",
            file=sys.stderr,
        )
    max_pending = args.max_pending or effective_concurrency * 4
    if max_pending < effective_concurrency:
        max_pending = effective_concurrency
    failures = 0
    failure_entries: list[str] = []
    cmd_lines: list[str] = []
    cmd_lock = threading.Lock()
    pcap_locks = {proxy: threading.Lock() for proxy in proxies}
    if args.pcap_middle_ports_base:
        base_middle_ports = parse_ports(args.pcap_middle_ports_base)
    else:
        base_middle_ports = []

    def iter_tasks() -> Iterable[tuple[str, int, str]]:
        idx = 0
        for url in urls:
            for count in range(1, args.times + 1):
                proxy = proxies[idx % len(proxies)]
                idx += 1
                yield url, count, proxy

    def worker(url: str, count: int, proxy: str) -> int:
        capture_proc = None
        run_dir = None
        output_path = None
        pcap_guard: contextlib.AbstractContextManager = contextlib.nullcontext()
        if args.pcap_dir is not None and args.pcap_serial:
            pcap_guard = pcap_locks[proxy]
        with pcap_guard:
            try:
                if args.pcap_dir is not None:
                    run_id = uuid.uuid4().hex[:8]
                    run_dir = Path(args.pcap_dir) / run_id
                    entry_port = None
                    exit_port = args.pcap_exit_port_base
                    middle_ports = list(base_middle_ports)
                    parsed = urlparse(proxy)
                    if parsed.port is not None:
                        entry_port = parsed.port
                    if entry_port is None:
                        entry_port = args.pcap_entry_port_base
                    if args.pcap_follow_proxy_ports and entry_port is not None:
                        offset = entry_port - args.pcap_entry_port_base
                        exit_port = args.pcap_exit_port_base + offset
                        middle_ports = [
                            port + offset for port in base_middle_ports
                        ]
                    if not middle_ports:
                        middle_ports = list(base_middle_ports)
                    capture_proc = start_capture(
                        run_dir,
                        entry_port=entry_port,
                        exit_port=exit_port,
                        middle_ports=middle_ports,
                    )
                    if args.pcap_wait > 0:
                        time.sleep(args.pcap_wait)
                if args.output_dir is not None:
                    output_path = Path(args.output_dir) / f"{url_label(url)}_{count}.html"
                cmd = build_curl_cmd(
                    url,
                    proxy,
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

    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        task_iter = iter_tasks()
        pending: dict[Future[int], tuple[str, int, str]] = {}

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
                url, count, _ = pending.pop(future)
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
