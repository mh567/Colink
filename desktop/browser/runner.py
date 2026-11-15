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
from typing import Any, BinaryIO, TextIO
from urllib.parse import SplitResult, urlsplit

REQUEST_LIMIT = 16 * 1024
ACTIONS = {
    "open": set(),
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
COMMAND_TIMEOUT = 5
MESSAGE_LIMIT = 16 * 1024 * 1024
TEXT_LIMIT = 60000
IMAGE_LIMIT = 3 * 1024 * 1024
LOOPBACK = "127.0.0.1"
TAB_ID = re.compile(r"[0-9a-zA-Z_-]{1,128}")
FORBIDDEN_FLAGS = {
    "--headless",
    "--remote-debugging-port",
    "--remote-debugging-address",
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
INSPECT_PAGE = r"""
(() => {
    let opaqueFrame = false;
    let sensitive = false;
    let truncated = false;
    let text = "";
    const limit = TEXT_BUDGET;
    const ready = (doc) => doc.readyState !== "loading" && Boolean(doc.body);
    const append = (part) => {
        const remaining = limit - text.length;
        if (part.length > remaining && part.slice(remaining).trim()) truncated = true;
        text += part.slice(0, remaining);
    };
    const lineBreak = () => {
        if (READ_TEXT && text && !text.endsWith("\n") && !truncated) append("\n");
    };
    const scan = (element, view, range) => {
        const style = view.getComputedStyle(element);
        if (style.display === "none" || style.opacity === "0" ||
            style.contentVisibility === "hidden") return;

        const visible = style.visibility === "visible";
        const tag = element.localName;
        if (tag === "input" && element.type === "password" && visible &&
            element.getClientRects().length) {
            sensitive = true;
            return;
        }
        if (tag === "iframe" || tag === "frame") {
            if (!visible || !element.getClientRects().length) return;
            const doc = element.contentDocument;
            const pendingNavigation = doc?.URL === "about:blank" &&
                !element.hasAttribute("srcdoc") && element.getAttribute("src") &&
                element.src !== "about:blank";
            if (!doc || !ready(doc) || pendingNavigation) {
                opaqueFrame = true;
                return;
            }
            lineBreak();
            scan(doc.documentElement, doc.defaultView, doc.createRange());
            lineBreak();
            return;
        }
        if (tag === "textarea" || tag === "select") return;
        const separate = tag === "br" ||
            (style.display !== "contents" && !style.display.startsWith("inline"));
        if (separate) lineBreak();
        const assigned = tag === "slot" ? element.assignedNodes() : [];
        const nodes = element.shadowRoot ? element.shadowRoot.childNodes
            : assigned.length ? assigned : element.childNodes;
        for (const node of nodes) {
            if (node.nodeType === Node.ELEMENT_NODE) {
                scan(node, view, range);
                if (sensitive) return;
            } else if (READ_TEXT && !truncated && visible && node.nodeType === Node.TEXT_NODE) {
                range.selectNodeContents(node);
                if (range.getClientRects().length) {
                    append(style.whiteSpaceCollapse === "collapse"
                        ? node.data.replace(/\s+/g, " ") : node.data);
                }
            }
        }
        if (separate) lineBreak();
    };
    if (document.documentElement) scan(document.documentElement, window, document.createRange());
    return {
        url: location.href, title: document.title, sensitive, opaqueFrame,
        ready: ready(document) && document.readyState === "complete",
        document: performance.timeOrigin,
        text: truncated ? text.replace(/[\uD800-\uDBFF]$/, "") : text, truncated,
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


# Map the inherited descriptors in a single-threaded process before replacing it
# with Chromium. Popen's preexec_fn is unsafe with the worker's asyncio threads.
POSIX_PIPE_EXEC = """
import fcntl
import os
import signal
import sys

inherited = [int(arg) for arg in sys.argv[1:3]]
copies = [fcntl.fcntl(fd, fcntl.F_DUPFD, 5) for fd in inherited]
for fd in inherited:
    os.close(fd)
for source, destination in zip(copies, (3, 4)):
    os.dup2(source, destination)
    os.close(source)
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
os.execv(sys.argv[3], sys.argv[3:])
"""


class PipeClient:
    def __init__(self, reader: BinaryIO, writer: BinaryIO):
        self.reader = reader
        self.writer = writer
        self.buffer = bytearray()
        self.next_id = 0
        self.usable = True

    def exchange(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = memoryview(json.dumps(command).encode("utf-8") + b"\0")
        while payload:
            count = self.writer.write(payload)
            if not count:
                raise OSError("DevTools pipe closed")
            payload = payload[count:]
        offset = 0
        while True:
            end = self.buffer.find(b"\0", offset)
            if end == -1:
                offset = len(self.buffer)
                chunk = self.reader.read(65536)
                if not chunk or offset + len(chunk) > MESSAGE_LIMIT:
                    raise OSError("DevTools pipe message unavailable")
                self.buffer.extend(chunk)
                continue
            message = json.loads(self.buffer[:end])
            del self.buffer[: end + 1]
            offset = 0
            if not isinstance(message, dict):
                raise ValueError("Invalid DevTools pipe message")
            if message.get("id") == command["id"] and message.get("sessionId") == command.get(
                "sessionId"
            ):
                return message

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str = "",
        timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        from octop_browser.cdp.client import CDPSessionError

        if not self.usable:
            raise CDPSessionError(method)
        self.next_id += 1
        command = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            command["sessionId"] = session_id
        try:
            async with asyncio.timeout(timeout):
                response = await asyncio.to_thread(self.exchange, command)
        except (OSError, ValueError, TimeoutError):
            self.usable = False
            raise CDPSessionError(method) from None
        except asyncio.CancelledError:
            self.usable = False
            raise
        if "error" in response:
            raise CDPSessionError(method)
        return response["result"]


class PageSession:
    def __init__(self, client: PipeClient, session_id: str):
        self.client = client
        self.session_id = session_id

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.client.send(method, params, session_id=self.session_id)


def launch_browser(args: list[str]) -> tuple[subprocess.Popen[bytes], PipeClient, list[str]]:
    with contextlib.ExitStack() as pipes:
        child_read_fd, write_fd = os.pipe()
        pipes.callback(os.close, child_read_fd)
        writer = pipes.enter_context(os.fdopen(write_fd, "wb", buffering=0))
        read_fd, child_write_fd = os.pipe()
        pipes.callback(os.close, child_write_fd)
        reader = pipes.enter_context(os.fdopen(read_fd, "rb", buffering=0))

        extra_flags = []
        child_fds = (child_read_fd, child_write_fd)
        options: dict[str, Any] = {"close_fds": True}
        if sys.platform == "win32":
            import msvcrt

            handles = [msvcrt.get_osfhandle(fd) for fd in child_fds]
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": handles}
            options["startupinfo"] = startup
            extra_flags = [f"--remote-debugging-io-pipes={handles[0]},{handles[1]}"]
            command = [*args[:-1], *extra_flags, args[-1]]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
        else:
            options["pass_fds"] = child_fds
            command = [
                sys.executable,
                "-I",
                "-S",
                "-c",
                POSIX_PIPE_EXEC,
                *map(str, child_fds),
                *args,
            ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        # Keep only the worker ends open so browser exit produces EOF.
        pipes.pop_all()
        os.close(child_read_fd)
        os.close(child_write_fd)
        return process, PipeClient(reader, writer), extra_flags


class LocalBrowser:
    def __init__(self, profile: Path):
        self.profile = profile
        self.process: subprocess.Popen[bytes] | None = None
        self.client: PipeClient | None = None
        self.listed_urls: dict[str, str] = {}

    async def start(self) -> None:
        if (
            self.client is not None
            and self.client.usable
            and self.process is not None
            and self.process.poll() is None
        ):
            return
        await self.close()
        executable = browser_executable()
        if executable is None:
            raise BrowserError("BROWSER_NOT_INSTALLED")
        client = None
        try:
            if self.profile != self.profile.resolve():
                raise BrowserError("BROWSER_START_FAILED")
            self.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.profile != self.profile.resolve(strict=True):
                raise BrowserError("BROWSER_START_FAILED")
            self.process, client, extra_flags = launch_browser(
                [
                    str(executable),
                    f"--user-data-dir={self.profile}",
                    "--remote-debugging-pipe",
                    "--enable-automation",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-sync",
                    "about:blank",
                ],
            )
            async with asyncio.timeout(START_TIMEOUT):
                result = await client.send("Browser.getBrowserCommandLine", timeout=START_TIMEOUT)
                arguments = result.get("arguments", [])
                expected_flags = [
                    f"--user-data-dir={self.profile}",
                    "--remote-debugging-pipe",
                    *extra_flags,
                ]
                if (
                    "--enable-automation" not in arguments
                    or any(
                        [
                            arg
                            for arg in arguments
                            if arg.partition("=")[0] == flag.partition("=")[0]
                        ]
                        != [flag]
                        for flag in expected_flags
                    )
                    or (
                        not extra_flags
                        and any(
                            arg.partition("=")[0] == "--remote-debugging-io-pipes"
                            for arg in arguments
                        )
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
                client.usable = False
                self.client = client
            await self.close()
            raise BrowserError("BROWSER_START_FAILED") from None

    def connection(self) -> PipeClient:
        if (
            self.client is None
            or not self.client.usable
            or self.process is None
            or self.process.poll() is not None
        ):
            raise BrowserError("BROWSER_NOT_OPEN")
        return self.client

    async def tab_targets(self) -> list[dict[str, str]]:
        result = await self.connection().send("Target.getTargets")
        tabs = []
        for target in result.get("targetInfos", []):
            if target["type"] != "page" or not TAB_ID.fullmatch(target["targetId"]):
                continue
            url = target["url"] if display_url(target["url"]) else ""
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

    async def inspect(self, tab_id: str, expected: str, *, capture: bool) -> dict[str, Any]:
        from octop_browser.actions.capture import screenshot
        from octop_browser.cdp.client import CDPSessionError
        from octop_browser.dom.refs import RefCache

        expected = display_url(expected)
        source = (await self.selected_tab(tab_id))["url"]
        if (
            not expected
            or display_url(source) != expected
            or self.listed_urls.get(tab_id) != source
        ):
            raise BrowserError("BROWSER_READ_BLOCKED")

        client = self.connection()
        session_id = ""
        try:
            session_id = (
                await client.send("Target.attachToTarget", {"targetId": tab_id, "flatten": True})
            )["sessionId"]
            page = PageSession(client, session_id)
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
                text = before["text"].strip()
                reply["text"] = text[:TEXT_LIMIT]
                reply["incomplete"] = (
                    before["opaqueFrame"] or before["truncated"] or len(text) > TEXT_LIMIT
                )
                if not reply["text"]:
                    raise BrowserError("BROWSER_READ_BLOCKED")
            after = await self.inspect_page(page, context, source, read_text=False, capture=capture)
            if (
                not after["ready"]
                or before["document"] != after["document"]
                or (await self.selected_tab(tab_id))["url"] != source
            ):
                raise BrowserError("BROWSER_READ_BLOCKED")
            if not capture:
                reply["incomplete"] |= after["opaqueFrame"]
            return reply
        except (CDPSessionError, TimeoutError):
            raise BrowserError("BROWSER_READ_BLOCKED") from None
        finally:
            if session_id:
                with contextlib.suppress(CDPSessionError):
                    await client.send("Target.detachFromTarget", {"sessionId": session_id})

    async def wait_for_page(
        self, page: PageSession, source: str, *, capture: bool
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
        self, page: PageSession, context: int, source: str, *, read_text: bool, capture: bool
    ) -> dict[str, Any]:
        inspection = await page.send(
            "Runtime.evaluate",
            {
                "expression": INSPECT_PAGE.replace("READ_TEXT", str(read_text).lower()).replace(
                    "TEXT_BUDGET", str(TEXT_LIMIT)
                ),
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
        return result

    async def action(self, name: str, url: str, tab_id: str) -> dict[str, Any]:
        if name == "close":
            await self.close()
            return {}
        if name == "open":
            await self.start()
        if name == "new_tab":
            created = await self.connection().send(
                "Target.createTarget", {"url": url or "about:blank"}
            )
            return {"tabs": await self.tabs(), "tab_id": created["targetId"]}
        if name in {"select_tab", "close_tab"}:
            await self.selected_tab(tab_id)
            method = "Target.activateTarget" if name == "select_tab" else "Target.closeTarget"
            await self.connection().send(method, {"targetId": tab_id})
        if name in {"query", "capture"}:
            return await self.inspect(tab_id, url, capture=name == "capture")
        return {"tabs": await self.tabs()}

    async def close(self) -> None:
        client, process = self.client, self.process
        self.client, self.process = None, None
        self.listed_urls.clear()
        if client is not None and client.usable and process is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                await client.send("Browser.close", timeout=1)
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
        if client is not None:
            client.writer.close()
            client.reader.close()


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
    # Go passes this capability marker so older packaged workers fail before launch.
    parser.add_argument("--private-debug-pipe", action="store_true")
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
