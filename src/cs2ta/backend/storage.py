"""Artifact boundary. A remote adapter can stage inputs and publish outputs."""

from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit


class ArtifactStore(Protocol):
    @property
    def cache_root(self) -> Path: ...

    def source(self, uri: str) -> AbstractContextManager[Path]: ...

    def dataset(self, uri: str) -> AbstractContextManager[Path]: ...

    def publish(self, path: Path) -> str: ...


class LocalArtifactStore:
    def __init__(self, cache_root: Path):
        self.cache_root = cache_root.expanduser().resolve()

    @staticmethod
    def file_path(uri: str) -> Path:
        parts = urlsplit(uri)
        if parts.scheme != "file" or parts.netloc or parts.query or parts.fragment:
            raise ValueError("本地存储只接受无主机名的 file URI。")
        path = Path(unquote(parts.path))
        if not path.is_absolute():
            raise ValueError("文件 URI 必须为绝对路径。")
        return path.resolve()

    @contextmanager
    def source(self, uri: str):
        yield self.file_path(uri)

    @contextmanager
    def dataset(self, uri: str):
        path = self.file_path(uri)
        if not path.is_relative_to(self.cache_root) or path == self.cache_root:
            raise ValueError("数据集必须位于当前环境的数据目录内。")
        yield path

    def publish(self, path: Path) -> str:
        with self.dataset(path.resolve().as_uri()) as resolved:
            if not (resolved / "manifest.json").is_file():
                raise ValueError("不能发布未完成的数据集。")
            return resolved.as_uri()
