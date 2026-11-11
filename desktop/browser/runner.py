"""Private JSON-lines browser worker for the native desktop client."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, TextIO
from urllib.parse import SplitResult, urlsplit

if TYPE_CHECKING:
    from octop_browser.cdp.client import CDPClient


REQUEST_LIMIT = 16 * 1024
ACTIONS = {
    "open": {"url"},
    "list": set(),
    "new_tab": {"url"},
    "select_tab": {"tab_id"},
    "close_tab": {"tab_id"},
    "query": {"tab_id", "url"},
    "capture": {"tab_id", "url"},
    "close": set(),
}


class BrowserError(Exception):
    pass


def page_url(url: str) -> SplitResult:
    if any(ord(character) <= 32 or character == "\\" for character in url):
        raise BrowserError("BROWSER_INVALID_URL")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return parsed
    except ValueError:
        pass
    raise BrowserError("BROWSER_INVALID_URL")


def request_args(line: bytes) -> tuple[str, str, str]:
    try:
        value = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):
        raise BrowserError("BROWSER_INVALID_REQUEST") from None
    if not isinstance(value, dict):
        raise BrowserError("BROWSER_INVALID_REQUEST")
    action = value.get("action")
    if (
        not isinstance(action, str)
        or action not in ACTIONS
        or value.keys() - {"action"} - ACTIONS[action]
    ):
        raise BrowserError("BROWSER_INVALID_REQUEST")
    url = value.get("url", "")
    tab_id = value.get("tab_id", "")
    if not isinstance(url, str) or not isinstance(tab_id, str):
        raise BrowserError("BROWSER_INVALID_REQUEST")
    if "tab_id" in ACTIONS[action] and not tab_id:
        raise BrowserError("BROWSER_INVALID_REQUEST")
    if action in {"query", "capture"} or url:
        page_url(url)
    return action, url, tab_id


START_TIMEOUT = 12
TEXT_LIMIT = 60000
IMAGE_LIMIT = 3 * 1024 * 1024
LOOPBACK = "127.0.0.1"
BROWSER_PATH = re.compile(
    r"/devtools/browser/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE
)
TAB_ID = re.compile(r"[0-9a-zA-Z_-]{1,128}")
FORBIDDEN_FLAGS = {
    "--headless",
    "--remote-debugging-pipe",
    "--remote-allow-origins",
    "--no-sandbox",
    "--no-zygote",
    "--no-zygote-sandbox",
    "--disable-setuid-sandbox",
    "--disable-seccomp-filter-sandbox",
    "--disable-namespace-sandbox",
    "--disable-gpu-sandbox",
    "--single-process",
}
INSPECT_PAGE = """
(() => {
    const visible = (element) => element.getClientRects().length > 0 &&
        getComputedStyle(element).visibility === "visible" &&
        getComputedStyle(element).opacity !== "0";
    let opaqueFrame = false;
    const hasPassword = (doc) => {
        for (const input of doc.querySelectorAll("input[type=password i]")) {
            if (visible(input)) return true;
        }
        for (const frame of doc.querySelectorAll("iframe, frame")) {
            if (!visible(frame)) continue;
            if (!frame.contentDocument) opaqueFrame = true;
            else if (hasPassword(frame.contentDocument)) return true;
        }
        return false;
    };
    const sensitive = hasPassword(document);
    const ready = document.readyState !== "loading" && Boolean(document.body);
    return {
        url: location.href, title: document.title, sensitive, opaqueFrame, ready,
        document: performance.timeOrigin,
        text: READ_TEXT && ready && !sensitive && visible(document.body)
            ? document.body.innerText.slice(0, 120000)
            : "",
    };
})()
"""


def display_url(url: str) -> str:
    try:
        return page_url(url)._replace(query="", fragment="").geturl()
    except BrowserError:
        return ""


def browser_executable() -> Path | None:
    if sys.platform == "win32":
        roots = [
            os.environ.get(name)
            for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
        ]
        suffixes = (
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
        )
        candidates = [Path(root) / suffix for root in roots if root for suffix in suffixes]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    elif sys.platform.startswith("linux"):
        candidates = [Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")]
    else:
        candidates = []
    return next((path for path in candidates if path.is_absolute() and path.is_file()), None)


def active_port_file(path: Path) -> bytes:
    if path.is_symlink() or path.is_junction():
        raise BrowserError("BROWSER_START_FAILED")
    try:
        if not path.is_file():
            return b""
        with path.open("rb") as stream:
            return stream.read(256)
    except OSError:
        return b""


def debug_endpoint(data: bytes) -> tuple[int, str] | None:
    try:
        port, path = data.decode("ascii").splitlines()
        if port.isdecimal() and 0 < int(port) < 65536 and BROWSER_PATH.fullmatch(path):
            return int(port), path
    except ValueError:
        pass
    return None


class LocalBrowser:
    def __init__(self, profile: Path):
        self.profile = profile
        self.process: subprocess.Popen[bytes] | None = None
        self.client: CDPClient | None = None
        self.port = 0
        self.listed_urls: dict[str, str] = {}

    async def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        await self.close()
        executable = browser_executable()
        if executable is None:
            raise BrowserError("BROWSER_NOT_INSTALLED")
        client = None
        try:
            from octop_browser.cdp.client import CDPClient

            if self.profile != self.profile.resolve():
                raise BrowserError("BROWSER_START_FAILED")
            self.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.profile != self.profile.resolve(strict=True):
                raise BrowserError("BROWSER_START_FAILED")
            active_port = self.profile / "DevToolsActivePort"
            previous = active_port_file(active_port)
            self.process = subprocess.Popen(
                [
                    str(executable),
                    f"--user-data-dir={self.profile}",
                    "--remote-debugging-port=0",
                    f"--remote-debugging-address={LOOPBACK}",
                    "--enable-automation",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-sync",
                    "about:blank",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            async with asyncio.timeout(START_TIMEOUT):
                while self.process.poll() is None:
                    current = active_port_file(active_port)
                    endpoint = debug_endpoint(current) if current != previous else None
                    if endpoint:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise BrowserError("BROWSER_START_FAILED")
                self.port, path = endpoint
                client = CDPClient(timeout=5)
                await client.connect(self.address(path))
                result = await client.send("Browser.getBrowserCommandLine")
                arguments = result.get("arguments", [])
                expected_flags = {
                    "--user-data-dir": str(self.profile),
                    "--remote-debugging-port": "0",
                    "--remote-debugging-address": LOOPBACK,
                }
                if (
                    "--enable-automation" not in arguments
                    or any(
                        [arg for arg in arguments if arg.partition("=")[0] == name]
                        != [f"{name}={value}"]
                        for name, value in expected_flags.items()
                    )
                    or self.profile != self.profile.resolve(strict=True)
                    or any(argument.partition("=")[0] in FORBIDDEN_FLAGS for argument in arguments)
                    or self.process.poll() is not None
                ):
                    raise BrowserError("BROWSER_START_FAILED")
                self.client = client
                await client.send("Browser.setDownloadBehavior", {"behavior": "deny"})
        except Exception:
            if client is not None and self.client is None:
                with contextlib.suppress(Exception):
                    await client.close()
            await self.close()
            raise BrowserError("BROWSER_START_FAILED") from None

    def address(self, path: str) -> str:
        return f"ws://{LOOPBACK}:{self.port}{path}"

    def connection(self) -> CDPClient:
        if self.client is None or self.process is None or self.process.poll() is not None:
            raise BrowserError("BROWSER_NOT_OPEN")
        return self.client

    async def tab_targets(self) -> list[dict[str, str]]:
        result = await self.connection().send("Target.getTargets")
        tabs = []
        for target in result.get("targetInfos", []):
            if target["type"] != "page" or not TAB_ID.fullmatch(target["targetId"]):
                continue
            try:
                url = target["url"] if display_url(self.target_url(target["url"])) else ""
            except BrowserError:
                url = ""
            tabs.append(
                {
                    "id": target["targetId"],
                    "title": target["title"][:512] if url else "",
                    "url": url,
                }
            )
        return tabs

    async def tabs(self) -> list[dict[str, str]]:
        tabs = await self.tab_targets()
        self.listed_urls = {tab["id"]: tab["url"] for tab in tabs}
        return [{**tab, "url": display_url(tab["url"])} for tab in tabs]

    async def selected_tab(self, tab_id: str) -> dict[str, str]:
        for tab in await self.tab_targets():
            if tab["id"] == tab_id:
                return tab
        raise BrowserError("BROWSER_TAB_NOT_FOUND")

    def target_url(self, url: str) -> str:
        if not url:
            return "about:blank"
        parsed = page_url(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port == self.port:
            raise BrowserError("BROWSER_INVALID_URL")
        return url

    async def inspect(self, tab_id: str, expected: str, *, capture: bool) -> dict[str, Any]:
        from octop_browser.actions.capture import screenshot
        from octop_browser.cdp.client import CDPClient, CDPSessionError
        from octop_browser.dom.refs import RefCache

        expected = display_url(expected)
        source = (await self.selected_tab(tab_id))["url"]
        if (
            not expected
            or display_url(source) != expected
            or self.listed_urls.get(tab_id) != source
        ):
            raise BrowserError("BROWSER_READ_BLOCKED")

        page = CDPClient(timeout=5)
        try:
            await page.connect(self.address(f"/devtools/page/{tab_id}"))
            context, before = await self.wait_for_page(page, source, capture=capture)
            reply = {"title": before["title"][:512], "url": expected}
            if capture:
                with tempfile.TemporaryDirectory() as directory:
                    image_path = Path(directory) / "page.png"
                    result = await screenshot(page, RefCache(), path=str(image_path))
                    if not result.success:
                        raise BrowserError("BROWSER_TASK_FAILED")
                    with image_path.open("rb") as stream:
                        image = stream.read(IMAGE_LIMIT + 1)
                if len(image) > IMAGE_LIMIT:
                    raise BrowserError("BROWSER_IMAGE_TOO_LARGE")
                if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise BrowserError("BROWSER_TASK_FAILED")
                reply["image"] = base64.b64encode(image).decode("ascii")
            else:
                reply["text"] = before["text"].strip()[:TEXT_LIMIT]
                if not reply["text"]:
                    raise BrowserError("BROWSER_READ_BLOCKED")
            after = await self.inspect_page(page, context, source, read_text=False, capture=capture)
            if (
                not after["ready"]
                or before["document"] != after["document"]
                or (await self.selected_tab(tab_id))["url"] != source
            ):
                raise BrowserError("BROWSER_READ_BLOCKED")
            return reply
        except (CDPSessionError, TimeoutError):
            raise BrowserError("BROWSER_READ_BLOCKED") from None
        finally:
            await page.close()

    async def wait_for_page(
        self, page: CDPClient, source: str, *, capture: bool
    ) -> tuple[int, dict[str, Any]]:
        # The target list can expose the requested address before the renderer commits it.
        async with asyncio.timeout(3):
            while True:
                frame = (await page.send("Page.getFrameTree"))["frameTree"]["frame"]
                if frame["url"] not in {"", "about:blank"}:
                    if display_url(frame["url"]) != display_url(source):
                        raise BrowserError("BROWSER_READ_BLOCKED")
                    world = await page.send(
                        "Page.createIsolatedWorld",
                        {"frameId": frame["id"], "worldName": "ColinkReadOnly"},
                    )
                    context = int(world["executionContextId"])
                    result = await self.inspect_page(
                        page, context, source, read_text=not capture, capture=capture
                    )
                    if result["ready"]:
                        return context, result
                await asyncio.sleep(0.05)

    async def inspect_page(
        self, page: CDPClient, context: int, source: str, *, read_text: bool, capture: bool
    ) -> dict[str, Any]:
        inspection = await page.send(
            "Runtime.evaluate",
            {
                "expression": INSPECT_PAGE.replace("READ_TEXT", str(read_text).lower()),
                "contextId": context,
                "returnByValue": True,
            },
        )
        result = inspection.get("result", {}).get("value")
        if (
            not isinstance(result, dict)
            or result["sensitive"]
            or (capture and result["opaqueFrame"])
            or result["url"] != source
        ):
            raise BrowserError("BROWSER_READ_BLOCKED")
        self.target_url(result["url"])
        return result

    async def action(self, name: str, url: str, tab_id: str) -> dict[str, Any]:
        if name == "close":
            await self.close()
            return {}
        if name == "open":
            await self.start()
        if name == "new_tab" or (name == "open" and url):
            await self.connection().send("Target.createTarget", {"url": self.target_url(url)})
        if name in {"select_tab", "close_tab"}:
            await self.selected_tab(tab_id)
            method = "Target.activateTarget" if name == "select_tab" else "Target.closeTarget"
            await self.connection().send(method, {"targetId": tab_id})
        if name in {"query", "capture"}:
            return await self.inspect(tab_id, url, capture=name == "capture")
        return {"tabs": await self.tabs()}

    async def close(self) -> None:
        client, process = self.client, self.process
        self.client, self.process, self.port = None, None, 0
        self.listed_urls.clear()
        if client is not None:
            if process is not None and process.poll() is None:
                with contextlib.suppress(Exception):
                    await client.send("Browser.close")
            with contextlib.suppress(Exception):
                await client.close()
        if process is not None:
            try:
                await asyncio.to_thread(process.wait, 2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        process.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        await asyncio.to_thread(process.wait, 2)


async def serve(profile: Path, source: BinaryIO, output: TextIO) -> None:
    browser = LocalBrowser(profile)
    try:
        while line := await asyncio.to_thread(source.readline, REQUEST_LIMIT + 1):
            action = ""
            try:
                if len(line) > REQUEST_LIMIT:
                    raise BrowserError("BROWSER_INVALID_REQUEST")
                action, url, tab_id = request_args(line)
                reply = await browser.action(action, url, tab_id)
            except BrowserError as exc:
                reply = {"error": str(exc)}
            except Exception:
                reply = {"error": "BROWSER_TASK_FAILED"}
            print(json.dumps(reply), file=output, flush=True)
            if len(line) > REQUEST_LIMIT or action == "close":
                break
    finally:
        await browser.close()


def local_connections_only(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in {LOOPBACK, "::1"}:
            raise OSError("BROWSER_TASK_FAILED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    args = parser.parse_args()
    if not args.profile.is_absolute():
        print(json.dumps({"error": "BROWSER_INVALID_REQUEST"}), flush=True)
        return 1
    for key in list(os.environ):
        if key.upper().startswith(("BROWSER_USE", "PLAYWRIGHT", "CHROME", "CHROMIUM")):
            os.environ.pop(key)
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    sys.addaudithook(local_connections_only)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
    asyncio.run(serve(args.profile, sys.stdin.buffer, sys.stdout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
