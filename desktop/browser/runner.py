"""Private JSON-lines browser worker for the native desktop client."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections import deque
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
    "forms": {"tab_id", "url"},
    "form_snapshot": {"tab_id", "url", "form_id", "submit_id"},
    "submit_form": {"tab_id", "url", "form_id", "submit_id", "snapshot_id", "values"},
    "prepare_upload": {"tab_id", "url", "selector"},
    "upload": {"tab_id", "url", "file_path", "selector"},
    "prepare_download": {"tab_id", "url", "download_url", "selector"},
    "download": {"tab_id", "url", "download_path", "download_url", "selector"},
    "close": set(),
}
FORM_ACTIONS = {"forms", "form_snapshot", "submit_form"}
FIELD_TYPES = {"text", "textarea", "select", "radio", "checkbox"}


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


def request_args(line: bytes) -> tuple[str, str, str, dict[str, Any]]:
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
    if "url" in ACTIONS[action] and (action != "new_tab" or url):
        page_url(url)
    extra: dict[str, Any] = {}
    if action in FORM_ACTIONS:
        extra = {key: value.get(key) for key in ACTIONS[action] - {"tab_id", "url"}}
        for key, item in extra.items():
            if key == "values":
                if (
                    not isinstance(item, dict)
                    or not item
                    or any(
                        not TAB_ID.fullmatch(field) or type(field_value) not in {str, bool}
                        for field, field_value in item.items()
                    )
                ):
                    raise BrowserError("BROWSER_INVALID_REQUEST")
            elif not isinstance(item, str) or not TAB_ID.fullmatch(item):
                raise BrowserError("BROWSER_INVALID_REQUEST")
    if action == "upload" or action == "download":
        field = "file_path" if action == "upload" else "download_path"
        raw = value.get(field)
        if not isinstance(raw, str) or not raw:
            raise BrowserError("BROWSER_INVALID_REQUEST")
        path = Path(raw)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not (path.is_file() if action == "upload" else path.is_dir())
        ):
            raise BrowserError("BROWSER_INVALID_REQUEST")
        extra[field] = str(path)
    if action.endswith(("upload", "download")):
        selector = value.get("selector", "")
        if (
            not isinstance(selector, str)
            or len(selector) > 1024
            or any(ord(c) < 32 for c in selector)
        ):
            raise BrowserError("BROWSER_INVALID_REQUEST")
        extra["selector"] = selector
    if action.endswith("download"):
        link = value.get("download_url", "")
        if not isinstance(link, str) or bool(link) == bool(selector):
            raise BrowserError("BROWSER_INVALID_REQUEST")
        if link:
            page_url(link)
        extra["download_url"] = link
    return action, url, tab_id, extra


START_TIMEOUT = 12
COMMAND_TIMEOUT = 5
DOWNLOAD_TIMEOUT = 25
TRANSFER_LIMIT = 20 * 1024 * 1024
DOWNLOAD_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
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

FORM_PAGE = r"""
(() => {
    const ids = new WeakMap();
    let nextId = 0;
    let snapshot;
    const id = (element) => {
        if (!ids.has(element)) ids.set(element, "c" + ++nextId);
        return ids.get(element);
    };
    const label = (element) =>
        element.getAttribute("aria-label")?.trim() ||
        Array.from(element.labels ?? [], (item) => item.innerText.trim()).filter(Boolean).join(" ") ||
        element.getAttribute("placeholder")?.trim() || element.name || id(element);
    const visible = (element) => {
        if (!element.getClientRects().length || element.closest("[inert]")) return false;
        for (let current = element; current; current = current.parentElement) {
            const style = getComputedStyle(current);
            if (style.visibility !== "visible" || style.opacity === "0" ||
                style.contentVisibility === "hidden") return false;
        }
        return true;
    };
    const available = (element) => !element.matches(":disabled") && visible(element);
    const writable = (element) => available(element) && !element.readOnly;
    const ownerDocument = Object.getOwnPropertyDescriptor(Node.prototype, "ownerDocument").get;
    const formElements = Object.getOwnPropertyDescriptor(HTMLFormElement.prototype, "elements").get;
    const formAction = Object.getOwnPropertyDescriptor(HTMLFormElement.prototype, "action").get;
    const attribute = (element, name) => Element.prototype.getAttribute.call(element, name);
    const attributes = (element, names) => names.map((name) => attribute(element, name));
    const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
    const listFields = (elements) => {
        const fields = [];
        const radios = new Map();
        for (const node of elements) {
            const tag = node.localName;
            if (tag === "input" && node.type === "radio") {
                const group = node.name || node;
                if (!radios.has(group)) radios.set(group, []);
                radios.get(group).push(node);
                continue;
            }
            if (!writable(node)) continue;
            let type, value, choices = [];
            if (tag === "textarea" || (tag === "input" && node.type === "text")) {
                if (/\b(?:username|current-password|new-password|one-time-code)\b/i.test(node.autocomplete)) continue;
                type = tag === "textarea" ? tag : "text";
                value = node.value;
            } else if (tag === "input" && node.type === "checkbox" && !node.indeterminate) {
                type = "checkbox";
                value = node.checked;
            } else if (tag === "select" && !node.multiple) {
                type = tag;
                choices = Array.from(node.options).filter((option) =>
                    !option.matches(":disabled") && !option.hidden && !option.closest("optgroup[hidden]"));
                const selected = node.options[node.selectedIndex];
                if (selected && !choices.includes(selected)) continue;
                value = selected?.value ?? "";
            } else continue;
            if (new Set(choices.map((choice) => choice.value)).size !== choices.length) continue;
            fields.push({
                nodes: [node],
                choices,
                data: {id: id(node), label: label(node), type, value, options:
                    choices.map((option) => ({label: option.label, value: option.value}))},
            });
        }
        for (const nodes of radios.values()) {
            if (!nodes.every(writable) ||
                new Set(nodes.map((node) => node.value)).size !== nodes.length) continue;
            const first = nodes[0];
            fields.push({
                nodes,
                choices: nodes,
                data: {id: id(first), label: label(first), type: "radio",
                    value: nodes.find((node) => node.checked)?.value ?? "",
                    options: nodes.map((node) => ({label: label(node), value: node.value}))},
            });
        }
        return fields;
    };
    const submitter = (form, node, baseTarget) => {
        const address = attribute(node, "formaction") === null ? formAction.call(form) : node.formAction;
        let url;
        try {
            url = new URL(address);
        } catch {
            return null;
        }
        if ((url.protocol !== "http:" && url.protocol !== "https:") ||
            url.username || url.password) return null;
        const action = url.href;
        url.search = new URLSearchParams(Array.from(url.searchParams, ([key, value]) => [
            key, /(?:token|secret|password|passwd|api[_-]?key|authorization|auth|session|cookie|csrf|xsrf|signature|^code$)/i.test(key)
                ? "[redacted]" : value,
        ])).toString();
        url.hash = "";

        const method = (attribute(node, "formmethod") ?? attribute(form, "method"))?.toLowerCase();
        const target = attribute(node, "formtarget") ?? attribute(form, "target") ?? baseTarget;
        return {
            node,
            action,
            data: {
                id: id(node),
                label: node.getAttribute("aria-label")?.trim() || node.innerText?.trim() ||
                    node.value || id(node),
                action_url: url.href,
                method: method === "post" || method === "dialog" ? method : "get",
                target: target?.includes("<") && /[\t\n\r]/.test(target) ? "_blank" : target || "_self",
            },
        };
    };
    const read = (form) => {
        const elements = [...formElements.call(form)];
        const baseTarget = document.querySelector("base[target]")?.getAttribute("target");
        const submitters = elements.filter((node) =>
            (node.localName === "button" || node.localName === "input") &&
            node.type === "submit" && available(node))
            .map((node) => submitter(form, node, baseTarget)).filter(Boolean);
        const fields = listFields(elements);
        const data = {
            id: id(form),
            label: attribute(form, "aria-label")?.trim() || attribute(form, "name") || id(form),
            fields: fields.map((field) => field.data),
            submitters: submitters.map(({data}) => data),
        };
        const structure = [
            document.baseURI, data.id, data.label,
            attributes(form, ["action", "method", "target", "enctype", "novalidate", "rel"]),
            submitters.map(({node, action, data}) => [action, data, node.value,
                attributes(node, ["type", "name", "formaction", "formmethod", "formtarget",
                    "formenctype", "formnovalidate"])]),
            fields.map(({nodes, choices, data: {value, ...field}}) => [field, choices.map(id),
                nodes.map((node) => [id(node), attributes(node, ["name", "maxlength",
                    "minlength", "pattern", "required"]), node.type === "checkbox" ? node.value : null])]),
        ];
        return {data, structure, submitters, fields};
    };
    const current = () => {
        const {form, document: originalDocument} = snapshot;
        if (document !== originalDocument || ownerDocument.call(form) !== originalDocument ||
            !Array.from(originalDocument.forms).includes(form)) return null;
        const item = read(form);
        return same(item.structure, snapshot.structure) ? item : null;
    };
    const fieldState = ({data, nodes}) => {
        if (data.type === "select") return nodes[0].selectedIndex;
        if (data.type === "radio") return nodes.findIndex((node) => node.checked);
        return data.value;
    };
    const matches = (item, values) => item.fields.every((field) =>
        fieldState(field) === values[field.data.id]);
    const methods = {
        list() {
            snapshot = undefined;
            const forms = [];
            let remaining = FORM_LIMIT;
            for (const form of document.forms) {
                const {data} = read(form);
                if (!data.fields.length || !data.submitters.length) continue;
                remaining -= JSON.stringify(data).length + 1;
                if (remaining < 0) return null;
                forms.push(data);
            }
            return forms;
        },
        snapshot(formId, submitId) {
            snapshot = undefined;
            const form = Array.from(document.forms).find((element) => ids.get(element) === formId);
            if (!form || ownerDocument.call(form) !== document) return null;
            const item = read(form);
            const selected = item.submitters.find(({data}) => data.id === submitId);
            if (!selected || !item.fields.length) return null;
            const {label, action_url, method, target} = selected.data;
            const result = {
                fields: item.data.fields,
                form_label: item.data.label,
                submit_label: label,
                action_url,
                method,
                target,
            };
            if (JSON.stringify(result).length > FORM_LIMIT) return null;
            snapshot = {
                document, form, submitter: selected.node, structure: item.structure,
                original: Object.fromEntries(item.fields.map((field) =>
                    [field.data.id, fieldState(field)])),
            };
            return result;
        },
        fill(values) {
            const item = snapshot && current();
            if (!item || !matches(item, snapshot.original)) return false;
            const entries = Object.entries(values);
            if (!entries.length) return false;
            const changes = [];
            const expected = {...snapshot.original};
            for (const [fieldId, value] of entries) {
                const field = item.fields.find(({data}) => data.id === fieldId);
                if (!field) return false;
                const {data, choices, nodes: [first]} = field;
                let node = first, property = "value", next = value, state = value;
                if (data.type === "checkbox") {
                    if (typeof value !== "boolean") return false;
                    property = "checked";
                } else if (typeof value !== "string") {
                    return false;
                } else if (data.type === "radio" || data.type === "select") {
                    const index = choices.findIndex((choice) => choice.value === value);
                    if (index < 0) return false;
                    if (data.type === "radio") {
                        node = choices[index];
                        property = "checked";
                        next = true;
                        state = index;
                    } else {
                        property = "selectedIndex";
                        next = state = choices[index].index;
                    }
                } else if ((data.type === "text" && /[\r\n]/.test(value)) ||
                           (node.maxLength >= 0 && value.length > node.maxLength)) {
                    return false;
                }
                if (expected[fieldId] !== state) {
                    changes.push([node, property, next]);
                    expected[fieldId] = state;
                }
            }
            snapshot.expected = expected;
            for (const [node, property, value] of changes) node[property] = value;
            for (const [node] of changes) {
                if (!node.isConnected) break;
                node.dispatchEvent(new Event("input", {bubbles: true, composed: true}));
                if (!node.isConnected) break;
                node.dispatchEvent(new Event("change", {bubbles: true}));
            }
            return true;
        },
        click() {
            const item = snapshot?.expected && current();
            const confirmed = snapshot;
            snapshot = undefined;
            if (!item || !matches(item, confirmed.expected)) return false;
            HTMLElement.prototype.click.call(confirmed.submitter);
            return true;
        },
    };
    return (source, action, ...args) => location.href === source &&
        document.readyState === "complete" ? methods[action](...args) : null;
})()
"""

FORM_CALL = "function(...args) { return this(...args); }"


def display_url(url: str) -> str:
    try:
        return page_url(url)._replace(query="", fragment="").geturl()
    except BrowserError:
        return ""


def _chrome_for_testing_candidates() -> list[Path]:
    candidates: list[Path] = []
    pw_roots: list[Path] = []
    if env_pw := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        pw_roots.append(Path(env_pw))
    if sys.platform == "darwin":
        pw_roots.extend(
            [
                Path.home() / "Library" / "Caches" / "ms-playwright",
                Path("/Library/Caches/ms-playwright"),
            ]
        )
        candidates.extend(
            [
                Path(
                    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
                ),
                Path.home()
                / "Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            ]
        )
    elif sys.platform == "win32":
        if local := os.environ.get("LOCALAPPDATA"):
            pw_roots.append(Path(local) / "ms-playwright")
        for root_name in (
            "ProgramW6432",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "LOCALAPPDATA",
        ):
            if root_val := os.environ.get(root_name):
                candidates.append(
                    Path(root_val) / "Google/Chrome for Testing/Application/chrome.exe"
                )
    elif sys.platform.startswith("linux"):
        pw_roots.extend(
            [
                Path.home() / ".cache" / "ms-playwright",
                Path("/root/.cache/ms-playwright"),
            ]
        )
        candidates.append(Path("/usr/bin/google-chrome-for-testing"))

    for root in pw_roots:
        if root.is_dir():
            try:
                cdirs = sorted(
                    [p for p in root.glob("chromium-*") if p.is_dir()],
                    key=lambda p: (
                        int(p.name.rsplit("-", 1)[-1])
                        if p.name.rsplit("-", 1)[-1].isdigit()
                        else -1
                    ),
                    reverse=True,
                )
                for cdir in cdirs:
                    if sys.platform == "darwin":
                        cft = (
                            "Google Chrome for Testing.app",
                            "Contents",
                            "MacOS",
                            "Google Chrome for Testing",
                        )
                        candidates.extend(
                            [
                                cdir.joinpath("chrome-mac-arm64", *cft),
                                cdir.joinpath("chrome-mac-x64", *cft),
                                cdir.joinpath(
                                    "chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"
                                ),
                            ]
                        )
                    elif sys.platform == "win32":
                        candidates.extend(
                            [
                                cdir / "chrome-win64" / "chrome.exe",
                                cdir / "chrome-win" / "chrome.exe",
                            ]
                        )
                    else:
                        candidates.extend(
                            [
                                cdir / "chrome-linux64" / "chrome",
                                cdir / "chrome-linux" / "chrome",
                            ]
                        )
            except OSError:
                pass

    return candidates


def browser_executable() -> Path | None:
    cft = _chrome_for_testing_candidates()
    if sys.platform == "win32":
        roots = [
            os.environ.get(name)
            for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
        ]
        suffixes = (
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
        )
        candidates = [*cft, *[Path(root) / suffix for root in roots if root for suffix in suffixes]]
    elif sys.platform == "darwin":
        candidates = [*cft, Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    elif sys.platform.startswith("linux"):
        candidates = [*cft, Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")]
    else:
        candidates = [*cft]
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
        self.download_events: deque[dict[str, Any]] | None = None

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
            if self.download_events is not None and message.get("method") in {
                "Browser.downloadWillBegin",
                "Browser.downloadProgress",
            }:
                if len(self.download_events) >= 128:
                    raise ValueError("Too many download events")
                self.download_events.append(message)
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


class FormSession(PageSession):
    def __init__(self, client: PipeClient, session_id: str, tab_id: str, source: str):
        super().__init__(client, session_id)
        self.tab_id = tab_id
        self.source = source
        self.loader = ""
        self.object_id = ""
        self.snapshot: tuple[str, str, str] | None = None

    async def frame(self) -> dict[str, Any]:
        return (await self.send("Page.getFrameTree"))["frameTree"]["frame"]

    async def start(self) -> None:
        frame = await self.frame()
        if display_url(frame["url"]) != display_url(self.source):
            raise BrowserError("BROWSER_READ_BLOCKED")
        self.loader = frame["loaderId"]
        world = await self.send(
            "Page.createIsolatedWorld", {"frameId": frame["id"], "worldName": "ColinkForms"}
        )
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": FORM_PAGE.replace("FORM_LIMIT", str(REQUEST_LIMIT)),
                "contextId": world["executionContextId"],
                "returnByValue": False,
            },
        )
        self.object_id = result["result"]["objectId"]

    async def call(self, action: str, *args: Any) -> Any:
        result = await self.send(
            "Runtime.callFunctionOn",
            {
                "objectId": self.object_id,
                "functionDeclaration": FORM_CALL,
                "arguments": [{"value": value} for value in (self.source, action, *args)],
                "returnByValue": True,
            },
        )
        if "exceptionDetails" in result:
            raise BrowserError("BROWSER_TASK_FAILED")
        return result.get("result", {}).get("value")


def public_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: field[key] for key in ("id", "label", "type", "value", "options")}
        for field in fields
        if field["type"] in FIELD_TYPES and not (field.get("disabled") or field.get("read_only"))
    ]


def bound_form_response(response: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(response)) + 1 > REQUEST_LIMIT:
        raise BrowserError("BROWSER_READ_BLOCKED")
    return response


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
        self.form_session: FormSession | None = None
        self.prepared: tuple[Any, ...] | None = None

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

    async def release_form_session(self) -> None:
        from octop_browser.cdp.client import CDPSessionError

        page, self.form_session = self.form_session, None
        if page is not None:
            with contextlib.suppress(CDPSessionError):
                await page.client.send("Target.detachFromTarget", {"sessionId": page.session_id})

    async def form_action(
        self, name: str, url: str, tab_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        from octop_browser.cdp.client import CDPSessionError

        blocked = "BROWSER_READ_BLOCKED" if name == "forms" else "BROWSER_FORM_STALE"
        started_writing = False
        completed = False
        try:
            tab = await self.selected_tab(tab_id)
            source = tab["url"]
            if not source or display_url(source) != url or self.listed_urls.get(tab_id) != source:
                raise BrowserError(blocked)
            evidence = {"title": tab["title"], "url": url}
            if name == "forms":
                await self.release_form_session()
                client = self.connection()
                attachment = await client.send(
                    "Target.attachToTarget", {"targetId": tab_id, "flatten": True}
                )
                page = self.form_session = FormSession(
                    client, attachment["sessionId"], tab_id, source
                )
                await page.start()
                forms = await page.call("list")
                if not isinstance(forms, list):
                    raise BrowserError(blocked)
                for form in forms:
                    form["fields"] = public_fields(form["fields"])
                response = bound_form_response({**evidence, "forms": forms})
            else:
                page = self.form_session
                if (
                    page is None
                    or (page.tab_id, page.source) != (tab_id, source)
                    or (await page.frame())["loaderId"] != page.loader
                ):
                    raise BrowserError(blocked)
                form_id, submit_id = args["form_id"], args["submit_id"]
                if name == "form_snapshot":
                    page.snapshot = None
                    snapshot = await page.call("snapshot", form_id, submit_id)
                    if not isinstance(snapshot, dict):
                        raise BrowserError(blocked)
                    snapshot_id = secrets.token_urlsafe(24)
                    response = bound_form_response(
                        {
                            **evidence,
                            "form_id": form_id,
                            "submit_id": submit_id,
                            "snapshot_id": snapshot_id,
                            "form_label": snapshot["form_label"],
                            "submit_label": snapshot["submit_label"],
                            "action_url": snapshot["action_url"],
                            "method": snapshot["method"],
                            "target": snapshot["target"],
                            "fields": public_fields(snapshot["fields"]),
                        }
                    )
                    page.snapshot = (snapshot_id, form_id, submit_id)
                else:
                    if page.snapshot != (args["snapshot_id"], form_id, submit_id):
                        raise BrowserError(blocked)
                    page.snapshot = None
                    started_writing = True
                    if await page.call("fill", args["values"]) is not True:
                        raise BrowserError(blocked)
                    if (
                        (await page.frame())["loaderId"] != page.loader
                        or (await self.selected_tab(tab_id))["url"] != source
                        or await page.call("click") is not True
                    ):
                        raise BrowserError("BROWSER_TASK_FAILED")
                    return {"click_dispatched": True}
            if (await page.frame())["loaderId"] != page.loader or (await self.selected_tab(tab_id))[
                "url"
            ] != source:
                raise BrowserError(blocked)
            completed = True
            return response
        except (CDPSessionError, TimeoutError):
            raise BrowserError("BROWSER_TASK_FAILED" if started_writing else blocked) from None
        except BrowserError as exc:
            if name != "forms" and not started_writing and str(exc) == "BROWSER_TAB_NOT_FOUND":
                raise BrowserError(blocked) from None
            raise
        finally:
            if not completed:
                await self.release_form_session()

    async def action(self, name: str, url: str, tab_id: str, **kwargs: Any) -> dict[str, Any]:
        if not name.endswith(("upload", "download")):
            self.prepared = None
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
            if self.form_session is not None and (name == "close_tab") == (
                self.form_session.tab_id == tab_id
            ):
                await self.release_form_session()
            method = "Target.activateTarget" if name == "select_tab" else "Target.closeTarget"
            await self.connection().send(method, {"targetId": tab_id})
        if name in {"query", "capture"}:
            return await self.inspect(tab_id, url, capture=name == "capture")
        if name in FORM_ACTIONS:
            return await self.form_action(name, display_url(url), tab_id, kwargs)
        if name.endswith(("upload", "download")):
            return await self.transfer(name, tab_id, url, **kwargs)
        return {"tabs": await self.tabs()}

    async def transfer(
        self,
        action: str,
        tab_id: str,
        expected: str,
        *,
        selector: str = "",
        file_path: str = "",
        download_path: str = "",
        download_url: str = "",
    ) -> dict[str, Any]:
        from octop_browser.cdp.client import CDPSessionError

        prepare = action.startswith("prepare_")
        kind = action.removeprefix("prepare_")
        approved, self.prepared = self.prepared, None
        if not prepare and approved is None:
            raise BrowserError("BROWSER_ACTION_CHANGED")
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
        initiated = False
        try:
            session_id = (
                await client.send("Target.attachToTarget", {"targetId": tab_id, "flatten": True})
            )["sessionId"]
            page = PageSession(client, session_id)
            context, before = await self.wait_for_page(page, source, capture=False)
            frame = (await page.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
            if kind == "upload":
                selector = selector or 'input[type="file"]'
                result = await page.send(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(() => { const el = document.querySelector("
                            + json.dumps(selector)
                            + "); return el?.localName === 'input' && el.type === 'file' && "
                            "!el.matches(':disabled') ? el : null; })()"
                        ),
                        "contextId": context,
                        "returnByValue": False,
                    },
                )
                node = result.get("result", {}).get("objectId")
                if not node:
                    raise BrowserError("BROWSER_ELEMENT_NOT_FOUND")
                target = (await page.send("DOM.describeNode", {"objectId": node}))["node"][
                    "backendNodeId"
                ]
                label = selector
            else:
                result = await page.send(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(() => { const el = "
                            + (
                                "document.querySelector(" + json.dumps(selector) + ")"
                                if selector
                                else "null"
                            )
                            + "; const href = "
                            + (
                                "el?.localName === 'a' && el.hasAttribute('href') ? el.href : ''"
                                if selector
                                else json.dumps(download_url)
                            )
                            + "; if (!href) return null; const url = new URL(href); "
                            "url.hash = ''; return url.origin === location.origin ? url.href : null; })()"
                        ),
                        "contextId": context,
                        "returnByValue": True,
                    },
                )
                target = result.get("result", {}).get("value")
                if not isinstance(target, str):
                    raise BrowserError("BROWSER_INVALID_URL")
                page_url(target)
                label = target
            intent = (kind, tab_id, source, before["document"], frame, selector, target)
            if prepare:
                self.prepared = intent
                return {"url": source, "title": before["title"][:512], "target": label}
            if approved != intent:
                raise BrowserError("BROWSER_ACTION_CHANGED")
            initiated = True
            if kind == "upload":
                await page.send("DOM.setFileInputFiles", {"files": [file_path], "objectId": node})
                # Chromium fires input/change itself. Report selection only: the site owns submission.
                return {"file": Path(file_path).name, "url": expected}
            artifact = await self.download(
                client, page, context, frame, target, Path(download_path)
            )
            return {"file": artifact.name, "size": artifact.stat().st_size, "url": expected}
        except CDPSessionError:
            raise BrowserError(
                "BROWSER_TRANSFER_UNCERTAIN" if initiated else "BROWSER_TASK_FAILED"
            ) from None
        except TimeoutError:
            raise BrowserError("BROWSER_TRANSFER_UNCERTAIN") from None
        finally:
            if session_id:
                with contextlib.suppress(CDPSessionError):
                    await client.send("Target.detachFromTarget", {"sessionId": session_id})

    async def download(
        self,
        client: PipeClient,
        page: PageSession,
        context: int,
        frame: str,
        expected: str,
        directory: Path,
    ) -> Path:
        # Reject redirects before the browser follows them: consent applies to the exact link.
        response = await client.send(
            "Runtime.evaluate",
            {
                "expression": """(async () => {
                    const response = await fetch("""
                + json.dumps(expected)
                + """, {
                        redirect: 'error', credentials: 'same-origin', mode: 'same-origin',
                        signal: AbortSignal.timeout(23000),
                    });
                    if (!response.ok || !response.body) return null;
                    const reader = response.body.getReader();
                    let size = 0;
                    const parts = [];
                    while (true) {
                        const {value, done} = await reader.read();
                        if (done) break;
                        size += value.byteLength;
                        if (size > """
                + str(TRANSFER_LIMIT)
                + """) {
                            await reader.cancel();
                            return 'too_large';
                        }
                        parts.push(value);
                    }
                    return URL.createObjectURL(new Blob(parts));
                })()""",
                "awaitPromise": True,
                "contextId": context,
                "returnByValue": True,
            },
            session_id=page.session_id,
            timeout=DOWNLOAD_TIMEOUT,
        )
        blob = response.get("result", {}).get("value")
        if blob == "too_large":
            raise BrowserError("BROWSER_FILE_TOO_LARGE")
        if not isinstance(blob, str) or not blob.startswith("blob:"):
            raise BrowserError("BROWSER_TRANSFER_UNCERTAIN")
        client.download_events = deque()
        guid = ""
        completed = False
        try:
            await client.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(directory),
                    "eventsEnabled": True,
                },
            )
            result = await page.send(
                "Runtime.evaluate",
                {
                    "expression": """(() => {
                    const a = document.createElement('a');
                    a.href = """
                    + json.dumps(blob)
                    + """;
                    a.download = '';
                    a.click();
                })()""",
                    "contextId": context,
                    "returnByValue": True,
                },
            )
            if "exceptionDetails" in result:
                raise BrowserError("BROWSER_TRANSFER_UNCERTAIN")
            async with asyncio.timeout(8):
                while True:
                    # The serial debug pipe receives events alongside command replies.
                    await client.send("Browser.getVersion")
                    while client.download_events:
                        event = client.download_events.popleft()
                        params = event["params"]
                        if event["method"] == "Browser.downloadWillBegin":
                            if guid or params["frameId"] != frame or params["url"] != blob:
                                raise BrowserError("BROWSER_TRANSFER_UNCERTAIN")
                            guid = params["guid"]
                            if not DOWNLOAD_ID.fullmatch(guid):
                                raise BrowserError("BROWSER_TASK_FAILED")
                            continue
                        if not guid or params.get("guid") != guid:
                            raise BrowserError("BROWSER_TRANSFER_UNCERTAIN")
                        if params.get("receivedBytes", 0) > TRANSFER_LIMIT:
                            raise BrowserError("BROWSER_FILE_TOO_LARGE")
                        if params["state"] == "canceled":
                            raise BrowserError("BROWSER_TASK_FAILED")
                        if params["state"] == "completed":
                            artifact = directory / guid
                            if artifact.is_symlink() or not artifact.is_file():
                                raise BrowserError("BROWSER_TASK_FAILED")
                            completed = True
                            return artifact
                    await asyncio.sleep(0.1)
        finally:
            client.download_events = None
            if guid and not completed:
                with contextlib.suppress(Exception):
                    await client.send("Browser.cancelDownload", {"guid": guid})
            await client.send("Browser.setDownloadBehavior", {"behavior": "deny"})
            with contextlib.suppress(Exception):
                await page.send(
                    "Runtime.evaluate",
                    {
                        "expression": "URL.revokeObjectURL(" + json.dumps(blob) + ")",
                        "contextId": context,
                    },
                )

    async def close(self) -> None:
        client, process = self.client, self.process
        self.client, self.process = None, None
        self.form_session = None
        self.listed_urls.clear()
        self.prepared = None
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
                action, url, tab_id, extra = request_args(line)
                reply = await browser.action(action, url, tab_id, **extra)
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
