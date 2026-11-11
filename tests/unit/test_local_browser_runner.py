"""The private browser worker protocol used by the desktop client."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "desktop" / "browser" / "runner.py"
_PNG = b"\x89PNG\r\n\x1a\nexample"
_BROWSER_PATH = "/devtools/browser/12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("local_browser_runner", _RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(runner: ModuleType, profile: Path, *commands: dict) -> list[dict]:
    request = io.BytesIO(b"".join(json.dumps(command).encode() + b"\n" for command in commands))
    response = io.StringIO()
    asyncio.run(runner.serve(profile.parent.resolve() / profile.name, request, response))
    return [json.loads(line) for line in response.getvalue().splitlines()]


def test_rejects_disallowed_url_before_starting_browser(runner, tmp_path, monkeypatch):
    def unexpected_start(*args, **kwargs):
        pytest.fail("invalid URL started the browser")

    monkeypatch.setattr(runner.subprocess, "Popen", unexpected_start)
    assert _request(
        runner,
        tmp_path,
        {"action": "open", "url": "file:///etc/passwd"},
        {"action": "new_tab", "url": "https://username:password@intranet.test/"},
        {"action": "open", "url": "https://intranet.test/\\@evil.test"},
        {"action": "click", "tab_id": "owned"},
    ) == [
        {"error": "BROWSER_INVALID_URL"},
        {"error": "BROWSER_INVALID_URL"},
        {"error": "BROWSER_INVALID_URL"},
        {"error": "BROWSER_INVALID_REQUEST"},
    ]


def test_missing_system_browser_fails_without_launching_any_process(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "browser_executable", lambda: None)

    def unexpected_start(*args, **kwargs):
        pytest.fail("missing browser started a subprocess")

    monkeypatch.setattr(runner.subprocess, "Popen", unexpected_start)
    assert _request(runner, tmp_path / "profiles", {"action": "open"}) == [
        {"error": "BROWSER_NOT_INSTALLED"}
    ]


@pytest.fixture
def browser(runner, tmp_path, monkeypatch):
    from octop_browser.cdp import client

    class Browser:
        args: list[str] = []
        commands: list[tuple[str, dict]] = []
        pages = {"A": "https://intranet.test/orders?token=private#result"}
        title = "订单"
        sensitive = False
        opaque_frame = False
        transient_blank = 0
        changed_source = False
        target_reads = 0
        redirect_after_capture = False
        extra_flag = None
        alive = False

        def __call__(self, args, **kwargs):
            self.args = args
            self.alive = True
            profile = Path(
                next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir="))
            )
            (profile / "DevToolsActivePort").write_text(
                f"34567\n{_BROWSER_PATH}\n", encoding="ascii"
            )
            return self

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout):
            if self.alive:
                raise runner.subprocess.TimeoutExpired(self.args, timeout)
            return 0

        def terminate(self):
            self.alive = False

        async def connect(self, address):
            assert address == f"ws://127.0.0.1:34567{_BROWSER_PATH}"

        async def send(self, method, params=None):
            params = params or {}
            self.commands.append((method, params))
            if method == "Browser.getBrowserCommandLine":
                return {
                    "arguments": [*self.args, self.extra_flag] if self.extra_flag else self.args
                }
            if method == "Target.getTargets":
                self.target_reads += 1
                if self.changed_source and self.target_reads == 2:
                    self.pages["A"] = "https://intranet.test/orders?token=changed#result"
                return {
                    "targetInfos": [
                        {"targetId": key, "type": "page", "title": self.title, "url": url}
                        for key, url in self.pages.items()
                    ]
                }
            if method == "Target.createTarget":
                self.pages["B"] = params["url"]
                return {"targetId": "B"}
            if method == "Target.closeTarget":
                del self.pages[params["targetId"]]
                return {"success": True}
            if method == "Browser.close":
                self.alive = False
            return {}

        async def close(self):
            pass

    browser = Browser()
    executable = tmp_path / "browser.exe"
    monkeypatch.setattr(runner, "browser_executable", lambda: executable)
    monkeypatch.setattr(runner.subprocess, "Popen", browser)

    class Page:
        async def connect(self, address):
            self.tab_id = address.rsplit("/", 1)[1]
            assert address == f"ws://127.0.0.1:34567/devtools/page/{self.tab_id}"

        async def send(self, method, params=None):
            params = params or {}
            browser.commands.append((method, params))
            if method == "Page.getFrameTree":
                url = browser.pages[self.tab_id]
                if browser.transient_blank:
                    url = "" if browser.transient_blank == 2 else "about:blank"
                    browser.transient_blank -= 1
                return {"frameTree": {"frame": {"id": "frame", "url": url}}}
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": 17}
            if method == "Runtime.evaluate":
                if "document: performance.timeOrigin" not in params["expression"]:
                    return {"result": {"value": "{}"}}
                return {
                    "result": {
                        "value": {
                            "url": browser.pages[self.tab_id],
                            "title": browser.title,
                            "sensitive": browser.sensitive,
                            "opaqueFrame": browser.opaque_frame,
                            "ready": True,
                            "text": "订单 123：已发货",
                            "document": 1,
                        }
                    }
                }
            if method == "Page.captureScreenshot":
                if browser.redirect_after_capture:
                    browser.pages[self.tab_id] = "https://other.test/login"
                return {"data": base64.b64encode(_PNG).decode("ascii")}
            return {}

        async def close(self):
            pass

    def connect(timeout):
        if not browser.connected:
            browser.connected = True
            return browser
        return Page()

    browser.connected = False
    monkeypatch.setattr(client, "CDPClient", connect)
    return browser


def test_owned_tabs_return_bounded_evidence_and_close_on_eof(runner, browser, tmp_path):
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
        {"action": "capture", "tab_id": "A", "url": "https://intranet.test/orders"},
        {"action": "new_tab", "url": "http://127.0.0.1:6543/query?q=123"},
        {"action": "select_tab", "tab_id": "B"},
        {"action": "close_tab", "tab_id": "B"},
    )
    original = {"id": "A", "title": "订单", "url": "https://intranet.test/orders"}
    assert replies[0] == {"tabs": [original]}
    assert replies[1] == {"title": "订单", "url": original["url"], "text": "订单 123：已发货"}
    assert base64.b64decode(replies[2]["image"]) == _PNG
    assert replies[3]["tabs"][1]["url"] == "http://127.0.0.1:6543/query"
    assert ("Target.activateTarget", {"targetId": "B"}) in browser.commands
    assert replies[5] == {"tabs": [original]}
    assert browser.args[0].endswith("browser.exe")
    assert "--remote-debugging-port=0" in browser.args
    assert "--remote-debugging-address=127.0.0.1" in browser.args
    assert "--no-sandbox" not in browser.args
    assert all(not name.startswith("Emulation.") for name, _ in browser.commands)
    assert not browser.alive


def test_refuses_login_mismatched_source_unknown_tab_and_debug_target(runner, browser, tmp_path):
    browser.sensitive = True
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
        {"action": "capture", "tab_id": "A", "url": "https://other.test/orders"},
        {"action": "query", "tab_id": "unknown", "url": "https://intranet.test/orders"},
        {"action": "new_tab", "url": "http://127.0.0.1:34567/json/version"},
    )
    assert replies[1:] == [
        {"error": "BROWSER_READ_BLOCKED"},
        {"error": "BROWSER_READ_BLOCKED"},
        {"error": "BROWSER_TAB_NOT_FOUND"},
        {"error": "BROWSER_INVALID_URL"},
    ]
    assert all(command != "Target.createTarget" for command, _ in browser.commands)


def test_refuses_profile_symlink(runner, tmp_path, monkeypatch):
    real_profile = tmp_path / "other-employee"
    real_profile.mkdir()
    profile = tmp_path / "profile"
    try:
        profile.symlink_to(real_profile, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    def unexpected_start(*args, **kwargs):
        pytest.fail("symlink profile started the browser")

    monkeypatch.setattr(runner, "browser_executable", lambda: tmp_path / "browser.exe")
    monkeypatch.setattr(runner.subprocess, "Popen", unexpected_start)
    assert _request(runner, profile, {"action": "open"}) == [{"error": "BROWSER_START_FAILED"}]


def test_discards_screenshot_after_navigation(runner, browser, tmp_path):
    browser.redirect_after_capture = True
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "capture", "tab_id": "A", "url": "https://intranet.test/orders"},
    )
    assert replies[1] == {"error": "BROWSER_READ_BLOCKED"}


def test_waits_for_document_commit_and_reads_in_isolated_world(runner, browser, tmp_path):
    browser.transient_blank = 2
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
    )
    assert replies[1]["text"] == "订单 123：已发货"
    evaluations = [params for method, params in browser.commands if method == "Runtime.evaluate"]
    assert evaluations and all(params.get("contextId") == 17 for params in evaluations)
    assert [method for method, _ in browser.commands].count("Page.getFrameTree") >= 3


def test_refuses_changed_query_parameters_and_opaque_frames(runner, browser, tmp_path):
    browser.changed_source = True
    browser.opaque_frame = True
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
        {"action": "list"},
        {"action": "capture", "tab_id": "A", "url": "https://intranet.test/orders"},
    )
    assert replies[1] == replies[3] == {"error": "BROWSER_READ_BLOCKED"}
    assert not any(method == "Page.captureScreenshot" for method, _ in browser.commands)


def test_rejects_browser_launch_with_different_debug_binding(runner, browser, tmp_path):
    browser.extra_flag = "--remote-debugging-address=0.0.0.0"
    replies = _request(runner, tmp_path / "profiles", {"action": "open"})
    assert replies == [{"error": "BROWSER_START_FAILED"}]
    assert not browser.alive
    assert not any(command == "Browser.close" for command, _ in browser.commands)
