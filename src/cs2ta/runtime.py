"""Explicit dev/prod settings, independent of Git branches and system Python."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tomllib


@dataclass(frozen=True)
class Settings:
    environment: str
    root: Path
    port: int
    data_dir: Path
    map_cache_dir: Path


def settings(root: Path | None = None, environment: str | None = None) -> Settings:
    root = (root or Path.cwd()).resolve()
    default = "prod" if (root / ".runtime/deployment.json").is_file() else "dev"
    environment = environment or os.environ.get("CS2TA_ENV", default)
    if environment not in {"dev", "prod"}:
        raise ValueError("CS2TA_ENV 必须为 dev 或 prod。")
    defaults = {"port": 8501 if environment == "dev" else 8502,
                "data_dir": f"data/{environment}", "map_cache_dir": f".cache/maps/{environment}"}
    path = root / "config/environments.toml"
    if path.is_file():
        defaults.update(tomllib.loads(path.read_text(encoding="utf-8")).get(environment, {}))
    local = root / f"config/local.{environment}.toml"
    if local.is_file():
        overrides = tomllib.loads(local.read_text(encoding="utf-8"))
        unknown = set(overrides) - set(defaults)
        if unknown:
            raise ValueError(f"未知环境配置：{sorted(unknown)}")
        defaults.update(overrides)
    port = defaults["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("端口需为 1024–65535 的整数。")

    def resolve(value):
        result = Path(value).expanduser()
        return (root / result).resolve() if not result.is_absolute() else result.resolve()

    data, maps = resolve(defaults["data_dir"]), resolve(defaults["map_cache_dir"])
    if data == maps or data in maps.parents or maps in data.parents:
        raise ValueError("数据目录与地图缓存目录不能相互覆盖。")
    return Settings(environment, root, port, data, maps)


def version_label(root: Path | None = None) -> str:
    root = root or Path.cwd()

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else ""

    commit = git("rev-parse", "--short=8", "HEAD") or "未提交"
    tag = git("describe", "--tags", "--exact-match", "HEAD")
    branch = git("branch", "--show-current")
    marker = root / ".runtime/deployment.json"
    if settings(root).environment == "prod" and marker.is_file():
        # Several releases may point to one commit; display the installed tag.
        tag = json.loads(marker.read_text(encoding="utf-8"))["version"]
    return f"{tag or branch or 'detached'} · {commit}"
