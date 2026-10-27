"""Local API and durable queue; parsing also runs without HTTP or SQLite."""

from cs2ta.runtime import settings


def development_settings(root=None):
    runtime = settings(root)
    if runtime.environment == "prod" or (runtime.root / ".runtime/deployment.json").is_file():
        raise ValueError("此开发入口不能用于 prod；请回到 dev 目录运行。")
    return runtime
