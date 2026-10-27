"""Shared browser and optional, per-run visual artifacts."""

from datetime import datetime, timezone
import shutil
import subprocess

import pytest

from visual_artifacts import VisualEvidence, write_gallery


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, "rep_" + call.when, outcome.get_result())


@pytest.fixture
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(
            headless=True, executable_path=shutil.which("chromium") or shutil.which("google-chrome"),
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1200, "height": 1100}, locale="zh-CN")
        yield page
        browser.close()


@pytest.fixture(autouse=True)
def visual_evidence(request, page):
    evidence = VisualEvidence(page, request.config._visual_root, request.node.nodeid, request.config._visual_entries)
    yield evidence
    report = getattr(request.node, "rep_call", None)
    status = report.outcome if report else "setup-error"
    if status == "failed" and evidence.enabled and not page.is_closed():
        evidence.snapshot("failure", "失败现场（测试未通过）")
    evidence.finish(status)


def pytest_sessionfinish(session, exitstatus):
    root = getattr(session.config, "_visual_root", None)
    if root is None:
        return
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    write_gallery(root, session.config._visual_entries,
                  {"commit": commit, "working_tree_dirty": bool(dirty), "exit_status": int(exitstatus),
                   "created_at": datetime.now(timezone.utc).isoformat()})
    if session.config._visual_entries:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_sep("=", f"Visual evidence: {root / 'index.html'}")
