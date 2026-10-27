"""Capture browser pixels as compact PNG/GIF evidence, without changing the UI."""

from contextlib import contextmanager
import hashlib
import html
import io
import json
from pathlib import Path
import re
import time

from PIL import Image


def slug(value):
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:100]


class VisualEvidence:
    def __init__(self, page, root, nodeid, entries):
        self.page, self.root, self.nodeid, self.entries = page, root, nodeid, entries
        self.enabled = root is not None
        self.source = {"kind": "synthetic", "description": "合成测试输入，验证播放器交互，不代表真实比赛。"}
        self.directory = None
        if self.enabled:
            self.directory = root / (slug(nodeid.split("::")[-1]) + "-" + hashlib.sha256(nodeid.encode()).hexdigest()[:8])
            self.directory.mkdir(parents=True, exist_ok=True)

    def pixels(self):
        image = Image.open(io.BytesIO(self.page.screenshot(animations="disabled"))).convert("RGB")
        image.thumbnail((960, 1000), Image.Resampling.LANCZOS)
        return image

    def _entry(self, path, caption, **metadata):
        entry = {"test": self.nodeid, "file": path.relative_to(self.root).as_posix(),
                 "caption": caption, "source": self.source.copy(), "status": "pending", **metadata}
        self.entries.append(entry)
        return path

    def snapshot(self, name, caption):
        if not self.enabled:
            return None
        path = self.directory / (slug(name) + ".png")
        image = self.pixels()
        image.save(path, optimize=True)
        return self._entry(path, caption, width=image.width, height=image.height)

    @contextmanager
    def animation(self, name, caption):
        clip = Clip(self)
        yield clip
        if self.enabled and clip.frames:
            path = self.directory / (slug(name) + ".gif")
            clip.frames[0].save(path, save_all=True, append_images=clip.frames[1:],
                                duration=clip.durations, loop=0, optimize=True, disposal=2)
            with Image.open(path) as saved:
                frame_count = saved.n_frames
            self._entry(path, caption, frames=frame_count, samples=clip.samples,
                        width=clip.frames[0].width, height=clip.frames[0].height)

    def finish(self, status):
        for entry in self.entries:
            if entry["test"] == self.nodeid:
                entry["status"] = status


class Clip:
    def __init__(self, evidence):
        self.evidence = evidence
        self.frames, self.durations, self.samples = [], [], []

    def capture(self, duration_ms=600):
        if not self.evidence.enabled:
            return
        self.frames.append(self.evidence.pixels().convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        self.durations.append(max(20, int(duration_ms / 10) * 10))
        self.samples.append(self.evidence.page.locator("canvas").evaluate(
            "canvas=>({tick:Number(canvas.dataset.renderTick),markers:Number(canvas.dataset.visibleMarkers),"
            "utilities:Number(canvas.dataset.visibleUtilities||0)})"))

    def playback(self, seconds=3, fps=6):
        if not self.evidence.enabled:
            return
        # Measure actual capture intervals; encode browser playback at that pace.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            started = time.monotonic()
            self.capture()
            remaining = 1 / fps - (time.monotonic() - started)
            if remaining > 0:
                self.evidence.page.wait_for_timeout(remaining * 1000)
            self.durations[-1] = max(20, round((time.monotonic() - started) * 100) * 10)


def write_gallery(root: Path, entries: list, provenance: dict):
    report = {**provenance, "artifacts": entries}
    (root / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cards = []
    for entry in entries:
        file = html.escape(entry["file"], quote=True)
        cards.append(f'<article><h2>{html.escape(entry["caption"])}</h2>'
                     f'<p>{html.escape(entry["status"])} · {html.escape(entry["test"])}</p>'
                     f'<p>{html.escape(entry["source"]["description"])}</p>'
                     f'<a href="{file}"><img src="{file}" loading="lazy" alt="{html.escape(entry["caption"], quote=True)}"></a></article>')
    document = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Playwright 功能验证</title><style>'
                'body{font:15px system-ui;background:#101827;color:#e5edf5;margin:24px auto;max-width:1100px;padding:0 16px}'
                'article{background:#1c293b;border-radius:12px;padding:16px;margin:20px 0}h2{font-size:18px}'
                'p{overflow-wrap:anywhere;color:#bfcfe0}img{display:block;max-width:100%;height:auto;border-radius:8px}'
                'a{color:#83dec4}</style><h1>Playwright 功能验证</h1>'
                f'<p>Commit: {html.escape(provenance["commit"])} · '
                f'测试退出码：{provenance["exit_status"]}</p>'
                '<p>图片来自 Chromium 实际截图；GIF 由连续截图或已断言的操作检查点组成。'
                '每项标注数据来源。截图用于辅助复核，测试断言记录在报告中。</p>'
                '<p><a href="manifest.json">查看机器可读记录</a></p>' + ''.join(cards) + '</html>')
    (root / "index.html").write_text(document, encoding="utf-8")
