"""Managed local servers with environment isolation and production guards."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

from cs2ta.release import production_ready, runtime_lock
from cs2ta.runtime import settings, version_label
from cs2ta.uploads import MAX_UPLOAD_MIB


def main() -> int:
    parser = argparse.ArgumentParser(description="启动独立 dev/prod 服务")
    parser.add_argument("environment", choices=["dev", "prod"])
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        config = settings(root, args.environment)
        if args.environment == "dev" and (root / ".runtime/deployment.json").is_file():
            raise ValueError("此目录是稳定版工作树，请用 scripts/run-prod.sh；开发请返回 dev 目录。")
        if args.environment == "prod":
            production_ready(root)
        os.environ["CS2TA_ENV"] = args.environment
        command = [sys.executable, "-m", "streamlit", "run", "app.py",
                   "--server.address", "127.0.0.1", "--server.port", str(config.port),
                   "--server.maxUploadSize", str(MAX_UPLOAD_MIB),
                   "--server.headless", "true", "--browser.gatherUsageStats", "false"]
        if args.environment == "prod":
            command.extend(["--server.fileWatcherType", "none", "--server.runOnSave", "false"])
        with runtime_lock(root) as lock:
            if args.environment == "dev" and (root / ".runtime/deployment.json").is_file():
                raise ValueError("稳定版工作树不能作为 dev 启动。")
            if args.environment == "prod":
                production_ready(root)  # Recheck after acquiring the update lock.
            print(f"{args.environment.upper()} · {version_label(root)}\nhttp://localhost:{config.port}\n数据：{config.data_dir}", flush=True)
            # The child retains the file lock even if the launcher is killed.
            child = subprocess.Popen(command, cwd=root, pass_fds=(lock.fileno(),))
            previous_handler = signal.signal(signal.SIGTERM, lambda *_: child.terminate() if child.poll() is None else None)
            try:
                return child.wait()
            except KeyboardInterrupt:
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait()
                return 130
            finally:
                signal.signal(signal.SIGTERM, previous_handler)
    except (OSError, ValueError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
