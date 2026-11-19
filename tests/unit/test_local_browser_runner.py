"""The private browser worker protocol used by the desktop client."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "desktop" / "browser" / "runner.py"
_PNG = b"\x89PNG\r\n\x1a\nexample"
_ORDERS = "https://intranet.test/orders"
_SAVE_DESTINATION = {
    "action_url": "https://intranet.test/save",
    "method": "post",
    "target": "_self",
}


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
        {"action": "new_tab", "url": "file:///etc/passwd"},
        {"action": "new_tab", "url": "https://username:password@intranet.test/"},
        {"action": "new_tab", "url": "https://intranet.test/\\@evil.test"},
        {"action": "new_tab", "url": "edge://sandbox"},
        {"action": "click", "tab_id": "owned"},
    ) == [
        {"error": "BROWSER_INVALID_URL"},
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


def test_open_rejects_a_navigation_address(runner, tmp_path, monkeypatch):
    async def unexpected_start(self):
        pytest.fail("open with an address started the browser")

    monkeypatch.setattr(runner.LocalBrowser, "start", unexpected_start)
    assert _request(runner, tmp_path, {"action": "open", "url": "https://intranet.test/"}) == [
        {"error": "BROWSER_INVALID_REQUEST"}
    ]


@pytest.fixture
def browser(runner, tmp_path, monkeypatch):
    class Browser:
        args: list[str] = []
        commands: list[tuple[str, dict]] = []
        pages = {"A": "https://intranet.test/orders?token=private#result"}
        title = "订单"
        sensitive = False
        opaque_frame = False
        truncated = False
        text = "订单 123：已发货"
        transient_blank = 0
        changed_source = False
        target_reads = 0
        redirect_after_capture = False
        extra_flag = None
        alive = False
        loader = "original"
        form_value = "42"
        accept_fill = True
        disconnect_after_click = False
        close_after_click = False
        clicks = 0

        def form_result(self, method, args):
            field = {
                "id": "quantity",
                "label": "Quantity",
                "type": "text",
                "value": self.form_value,
                "options": [],
            }
            candidate = {
                "id": "order",
                "label": "Order",
                "fields": [
                    field,
                    *[
                        {**field, "id": kind, "type": kind, "value": ""}
                        for kind in ("password", "hidden", "file", "button")
                    ],
                    {**field, "id": "disabled", "disabled": True, "value": ""},
                    {**field, "id": "readonly", "read_only": True, "value": ""},
                ],
                "submitters": [{"id": "save", "label": "Save", **_SAVE_DESTINATION}],
            }
            if method == "list":
                value = [candidate]
            elif method == "snapshot":
                assert args == ["order", "save"]
                value = {
                    "fields": candidate["fields"],
                    "form_label": "Order",
                    "submit_label": "Save",
                    **_SAVE_DESTINATION,
                }
            elif method == "fill":
                assert args == [{"quantity": "17"}]
                if self.accept_fill:
                    self.form_value = "17"
                value = True
            else:
                assert method == "click" and not args
                value = self.form_value == "17"
                if value:
                    self.clicks += 1
                    if self.close_after_click:
                        del self.pages["A"]
                    if self.disconnect_after_click:
                        raise OSError("lost response after click")
            return {"result": {"value": value}}

        def __call__(self, args, **kwargs):
            assert kwargs["close_fds"]
            if sys.platform == "win32":
                handles = kwargs["startupinfo"].lpAttributeList["handle_list"]
                assert len(handles) == 2 and all(os.get_handle_inheritable(h) for h in handles)
                assert f"--remote-debugging-io-pipes={handles[0]},{handles[1]}" in args
                self.args = args
            else:
                assert len(kwargs["pass_fds"]) == 2
                self.args = args[args.index(str(executable)) :]
            self.alive = True
            return self

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout):
            if self.alive:
                raise runner.subprocess.TimeoutExpired(self.args, timeout)
            return 0

        def terminate(self):
            self.alive = False

        def exchange(self, command):
            method, params = command["method"], command["params"]
            session_id = command.get("sessionId")
            self.commands.append((method, params))
            if method.startswith(("Page.", "Runtime.", "DOM.")):
                assert session_id == "session:A"
                result = self.page_result(method, params)
            else:
                assert session_id is None
                result = self.browser_result(method, params)
            return {"id": command["id"], "sessionId": session_id, "result": result}

        def browser_result(self, method, params):
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
                self.pages["C"] = params["url"]
                self.pages["B"] = params["url"]
                return {"targetId": "B"}
            if method == "Target.attachToTarget":
                assert params == {"targetId": "A", "flatten": True}
                return {"sessionId": "session:A"}
            if method == "Target.detachFromTarget":
                assert params == {"sessionId": "session:A"}
            if method == "Target.closeTarget":
                del self.pages[params["targetId"]]
                return {"success": True}
            if method == "Browser.setDownloadBehavior":
                self.download_behavior = params
                if params["behavior"] == "allowAndName":
                    self.last_allowed_download = params
            if method == "Browser.close":
                self.alive = False
            return {}

        def page_result(self, method, params):
            if method == "Page.getFrameTree":
                url = self.pages["A"]
                if self.transient_blank:
                    url = "" if self.transient_blank == 2 else "about:blank"
                    self.transient_blank -= 1
                return {
                    "frameTree": {"frame": {"id": "frame", "loaderId": self.loader, "url": url}}
                }
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": 17}
            if method == "Runtime.callFunctionOn":
                source, action, *args = [item["value"] for item in params["arguments"]]
                assert source == "https://intranet.test/orders?token=private#result"
                assert params["objectId"] == "form-page"
                return self.form_result(action, args)
            if method == "DOM.describeNode":
                return {"node": {"backendNodeId": getattr(self, "node_id", 1)}}
            if method == "DOM.setFileInputFiles":
                self.uploaded_files = params["files"]
                return {}
            if method == "Runtime.evaluate":
                expr = params["expression"]
                if "URL.createObjectURL" in expr:
                    return {"result": {"value": "blob:https://intranet.test/approved"}}
                if "url.origin === location.origin" in expr:
                    return {"result": {"value": getattr(self, "target_url", None)}}
                if "el?.localName === 'input' && el.type === 'file'" in expr:
                    if getattr(self, "element_not_found", False):
                        return {"result": {"subtype": "null"}}
                    return {"result": {"objectId": "obj:file_input"}}
                if params.get("returnByValue") is False:
                    return {"result": {"objectId": "form-page"}}
                if "document: performance.timeOrigin" not in expr:
                    if "a.click();" in expr and hasattr(self, "on_evaluate"):
                        self.on_evaluate()
                    return {"result": {"value": "{}"}}
                return {
                    "result": {
                        "value": {
                            "url": self.pages["A"],
                            "title": self.title,
                            "sensitive": self.sensitive,
                            "opaqueFrame": self.opaque_frame,
                            "ready": True,
                            "text": self.text,
                            "truncated": self.truncated,
                            "document": 1,
                        }
                    }
                }
            if method == "Page.captureScreenshot":
                if self.redirect_after_capture:
                    self.pages["A"] = "https://other.test/login"
                return {"data": base64.b64encode(_PNG).decode("ascii")}
            return {}

    browser = Browser()
    executable = tmp_path / "browser.exe"
    monkeypatch.setattr(runner, "browser_executable", lambda: executable)
    monkeypatch.setattr(runner.subprocess, "Popen", browser)

    def exchange(client, command):
        browser.client = client
        return browser.exchange(command)

    monkeypatch.setattr(runner.PipeClient, "exchange", exchange)

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
        {"action": "close_tab", "tab_id": "C"},
    )
    original = {"id": "A", "title": "订单", "url": "https://intranet.test/orders"}
    assert replies[0] == {"tabs": [original]}
    assert replies[1] == {
        "title": "订单",
        "url": original["url"],
        "text": "订单 123：已发货",
        "incomplete": False,
    }
    assert base64.b64decode(replies[2]["image"]) == _PNG
    assert replies[3]["tabs"][1]["url"] == "http://127.0.0.1:6543/query"
    assert replies[3]["tab_id"] == "B"
    assert ("Target.activateTarget", {"targetId": "B"}) in browser.commands
    assert browser.commands.count(("Target.detachFromTarget", {"sessionId": "session:A"})) == 2
    assert replies[6] == {"tabs": [original]}
    assert browser.args[0].endswith("browser.exe")
    assert browser.args[-1] == "about:blank"
    assert "--remote-debugging-pipe" in browser.args
    assert not any(arg.startswith("--remote-debugging-port") for arg in browser.args)
    assert not any(arg.startswith("--remote-debugging-address") for arg in browser.args)
    assert not (tmp_path / "profiles" / "DevToolsActivePort").exists()
    assert "--no-sandbox" not in browser.args
    assert all(not name.startswith("Emulation.") for name, _ in browser.commands)
    assert not browser.alive


@pytest.mark.parametrize("address", [{}, {"url": ""}])
def test_can_open_a_blank_tab_without_a_web_address(runner, browser, tmp_path, address):
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "new_tab", **address},
    )
    assert replies[1]["tab_id"] == "B"
    assert {"id": "B", "title": "", "url": ""} in replies[1]["tabs"]
    assert ("Target.createTarget", {"url": "about:blank"}) in browser.commands


def test_refuses_login_mismatched_source_and_unknown_tab(runner, browser, tmp_path):
    browser.sensitive = True
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
        {"action": "capture", "tab_id": "A", "url": "https://other.test/orders"},
        {"action": "query", "tab_id": "unknown", "url": "https://intranet.test/orders"},
    )
    assert replies[1:] == [
        {"error": "BROWSER_READ_BLOCKED"},
        {"error": "BROWSER_READ_BLOCKED"},
        {"error": "BROWSER_TAB_NOT_FOUND"},
    ]
    assert all(command != "Target.createTarget" for command, _ in browser.commands)


def _form_calls(browser):
    return [
        params["arguments"][1]["value"]
        for method, params in browser.commands
        if method == "Runtime.callFunctionOn"
    ]


def _with_form_session(runner, profile, scenario):
    async def run():
        session = runner.LocalBrowser(profile)
        try:
            await session.action("open", "", "")
            return await scenario(session)
        finally:
            await session.close()

    return asyncio.run(run())


async def _form_snapshot(session):
    forms = await session.action("forms", _ORDERS, "A")
    form = forms["forms"][0]
    target = {"form_id": form["id"], "submit_id": form["submitters"][0]["id"]}
    snapshot = await session.action("form_snapshot", _ORDERS, "A", **target)
    return forms, snapshot, target


def test_forms_skip_sensitive_and_non_writable_controls(runner, browser, tmp_path):
    forms, snapshot, _ = _with_form_session(runner, tmp_path / "profile", _form_snapshot)
    expected = {"id": "quantity", "label": "Quantity", "type": "text", "value": "42", "options": []}
    assert forms == {
        "title": "订单",
        "url": _ORDERS,
        "forms": [
            {
                "id": "order",
                "label": "Order",
                "fields": [expected],
                "submitters": [{"id": "save", "label": "Save", **_SAVE_DESTINATION}],
            }
        ],
    }
    assert snapshot["fields"] == [expected]
    assert snapshot["form_label"] == "Order" and snapshot["submit_label"] == "Save"
    assert {key: snapshot[key] for key in _SAVE_DESTINATION} == _SAVE_DESTINATION
    assert _form_calls(browser) == ["list", "snapshot"]
    assert browser.clicks == 0


def test_submit_form_rejects_stale_snapshot_without_click(runner, browser, tmp_path):
    async def scenario(session):
        _, snapshot, target = await _form_snapshot(session)
        browser.loader = "reloaded"
        with pytest.raises(runner.BrowserError, match="^BROWSER_FORM_STALE$"):
            await session.action(
                "submit_form",
                _ORDERS,
                "A",
                **target,
                snapshot_id=snapshot["snapshot_id"],
                values={"quantity": "17"},
            )

    _with_form_session(runner, tmp_path / "profile", scenario)
    assert _form_calls(browser) == ["list", "snapshot"]
    assert browser.form_value == "42" and browser.clicks == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"script": "document.forms[0].submit()"},
        {"selector": "#save"},
        {"values": {"quantity": 17}},
        {"values": {"quantity": "a" * (16 * 1024)}},
    ],
)
def test_submit_form_rejects_unstructured_or_oversized_worker_requests(
    runner, browser, tmp_path, extra
):
    assert _request(
        runner,
        tmp_path / "profile",
        {"action": "open"},
        {
            "action": "submit_form",
            "url": _ORDERS,
            "tab_id": "A",
            "form_id": "order",
            "submit_id": "save",
            "snapshot_id": "snapshot",
            "values": {"quantity": "17"},
            **extra,
        },
    )[1] == {"error": "BROWSER_INVALID_REQUEST"}
    assert _form_calls(browser) == []


def test_form_snapshot_rejects_oversized_fields(runner, browser, tmp_path):
    async def scenario(session):
        forms = await session.action("forms", _ORDERS, "A")
        form = forms["forms"][0]
        browser.form_value = "测试" * 1400
        with pytest.raises(runner.BrowserError, match="^BROWSER_READ_BLOCKED$"):
            await session.action(
                "form_snapshot", _ORDERS, "A", form_id=form["id"], submit_id="save"
            )

    _with_form_session(runner, tmp_path / "profile", scenario)
    assert _form_calls(browser) == ["list", "snapshot"]
    assert browser.clicks == 0


@pytest.mark.parametrize("accept_fill", [False, True])
def test_submit_form_verifies_filled_values_before_single_click(
    runner, browser, tmp_path, accept_fill
):
    browser.accept_fill = accept_fill
    browser.close_after_click = True

    async def scenario(session):
        _, snapshot, target = await _form_snapshot(session)
        payload = {**target, "snapshot_id": snapshot["snapshot_id"], "values": {"quantity": "17"}}
        if accept_fill:
            assert await session.action("submit_form", _ORDERS, "A", **payload) == {
                "click_dispatched": True,
            }
        else:
            with pytest.raises(runner.BrowserError, match="^BROWSER_TASK_FAILED$"):
                await session.action("submit_form", _ORDERS, "A", **payload)
        with pytest.raises(runner.BrowserError, match="^BROWSER_FORM_STALE$"):
            await session.action("submit_form", _ORDERS, "A", **payload)

    _with_form_session(runner, tmp_path / "profile", scenario)
    assert _form_calls(browser) == ["list", "snapshot", "fill", "click"]
    assert browser.form_value == ("17" if accept_fill else "42")
    assert browser.clicks == int(accept_fill)


def test_submit_form_does_not_replay_after_cdp_disconnect(runner, browser, tmp_path):
    browser.disconnect_after_click = True

    async def scenario(session):
        _, snapshot, target = await _form_snapshot(session)
        payload = {**target, "snapshot_id": snapshot["snapshot_id"], "values": {"quantity": "17"}}
        for error in ("BROWSER_TASK_FAILED", "BROWSER_NOT_OPEN"):
            with pytest.raises(runner.BrowserError, match=f"^{error}$"):
                await session.action("submit_form", _ORDERS, "A", **payload)

    _with_form_session(runner, tmp_path / "profile", scenario)
    assert _form_calls(browser) == ["list", "snapshot", "fill", "click"]
    assert browser.clicks == 1


@pytest.fixture
def form_page_js(runner):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the fixed renderer script")

    fixture = r"""
const assert = require("node:assert/strict");
global.Node = class {
    get ownerDocument() { return this.document; }
    set ownerDocument(document) { this.document = document; }
};
class Element extends Node {
    constructor(localName, type, storedValue = "", attributes = {}) {
        super();
        Object.assign(this, {localName, type, storedValue, attributes, isConnected: true});
    }
    getAttribute(name) { return this.attributes[name] ?? null; }
    get value() {
        assert.ok(!this.protectedValue, "excluded input value read");
        return this.storedValue;
    }
    set value(value) { this.storedValue = value; }
    get formAction() { return this.resolvedAction("formaction"); }
    resolvedAction(name) {
        const action = this.getAttribute(name);
        return action ? new URL(action, this.ownerDocument.baseURI).href : this.ownerDocument.URL;
    }
    getClientRects() { return [1]; }
    closest() { return null; }
    matches(selector) { return selector === ":disabled" && Boolean(this.disabled); }
    dispatchEvent(event) { this.onEvent?.(event); }
    click() { this.clicks = (this.clicks ?? 0) + 1; }
}
global.HTMLElement = Element;
global.HTMLFormElement = class extends Element {
    get elements() { return this.controls; }
    get action() { return this.resolvedAction("action"); }
};
global.getComputedStyle = () => ({visibility: "visible", opacity: "1"});
global.location = {href: "https://intranet.test/orders?token=private#result"};
const quantity = new Element("input", "text", "42", {"aria-label": "Quantity"});
const excluded = ["password", "hidden", "file", "button"].map((type) =>
    Object.assign(new Element("input", type, "private"), {protectedValue: true}));
excluded.push(Object.assign(new Element("input", "text"), {disabled: true, protectedValue: true}));
excluded.push(Object.assign(new Element("input", "text"), {readOnly: true, protectedValue: true}));
const submitter = new Element("button", "submit", "", {"aria-label": "Save"});
const form = new HTMLFormElement("form", "", "", {
    action: "https://intranet.test/save?token=private#result", method: "POST",
});
form.controls = [quantity, ...excluded, submitter];
const base = new Element("base", "");
const originalDocument = {
    forms: [form], URL: location.href, baseURI: "https://intranet.test/team/", readyState: "complete",
    querySelector(selector) {
        assert.equal(selector, "base[target]");
        return base.getAttribute("target") === null ? null : base;
    },
};
global.document = originalDocument;
form.ownerDocument = submitter.ownerDocument = originalDocument;
"""
    script = runner.FORM_PAGE.replace("FORM_LIMIT", str(runner.REQUEST_LIMIT)).strip()
    helpers = """
const call = (...args) => formPage(location.href, ...args);
const selectForm = () => {
    const [candidate] = call("list");
    const [button] = candidate.submitters;
    const snapshot = call("snapshot", candidate.id, button.id);
    return {candidate, button, snapshot};
};
"""

    def run(checks):
        result = subprocess.run(
            [node, "-"],
            input=f"{fixture}\nconst formPage = {script};\n{helpers}\n{checks}",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    return run


def test_form_page_hides_sensitive_controls_and_stops_on_readback_mismatch(form_page_js):
    form_page_js("""
const {candidate, snapshot} = selectForm();
assert.deepEqual(candidate.fields.map((field) => field.label), ["Quantity"]);
assert.deepEqual(snapshot.fields.map((field) => field.value), ["42"]);
const [{id}] = snapshot.fields;
quantity.onEvent = () => { quantity.value = "page changed it"; };
assert.equal(call("fill", {[id]: "17"}), true);
assert.equal(quantity.value, "page changed it");
assert.equal(call("click"), false);
assert.equal(submitter.clicks ?? 0, 0);
""")


def test_form_page_exposes_redacted_effective_submit_destination(form_page_js):
    form_page_js("""
let {button, snapshot} = selectForm();
const destination = ({action_url, method, target}) => ({action_url, method, target});
assert.deepEqual(destination(button),
    {action_url: "https://intranet.test/save", method: "post", target: "_self"});
assert.deepEqual(destination(snapshot), destination(button));

base.attributes.target = "base-frame";
assert.equal(selectForm().button.target, "base-frame");
form.attributes.target = "_blank";
assert.equal(selectForm().button.target, "_blank");
Object.assign(submitter.attributes, {formaction: "preview?token=private#fragment",
    formmethod: "GET", formtarget: "preview"});
({button, snapshot} = selectForm());
assert.deepEqual(destination(button),
    {action_url: "https://intranet.test/team/preview", method: "get", target: "preview"});
assert.deepEqual(destination(snapshot), destination(button));

Object.assign(submitter.attributes, {formaction: "", formmethod: "DIALOG", formtarget: ""});
assert.deepEqual(destination(selectForm().button),
    {action_url: "https://intranet.test/orders", method: "dialog", target: "_self"});
""")


def test_form_page_rejects_destinations_with_credentials(form_page_js):
    form_page_js("""
const {candidate, button, snapshot} = selectForm();
submitter.attributes.formaction = "https://employee:password@intranet.test/save";
assert.equal(call("fill", {[snapshot.fields[0].id]: "17"}), false);
assert.equal(quantity.value, "42");
assert.equal(call("click"), false);
assert.deepEqual(call("list"), []);
assert.equal(call("snapshot", candidate.id, button.id), null);
delete submitter.attributes.formaction;
form.attributes.action = "https://employee@intranet.test/save";
assert.deepEqual(call("list"), []);
""")


@pytest.mark.parametrize(
    "mutation",
    [
        "form.ownerDocument = {...originalDocument}",
        "document.forms = []",
        "base.attributes.target = '_blank'",
        "form.attributes.action = form.attributes.action.replace('token=private', 'token=new')",
    ],
)
@pytest.mark.parametrize("phase", ["before_fill", "before_click"])
def test_form_page_rejects_stale_document_and_destination(form_page_js, mutation, phase):
    expected_fill = "true" if phase == "before_click" else "false"
    before = mutation if phase == "before_fill" else ""
    after = mutation if phase == "before_click" else ""
    form_page_js(f"""
const {{snapshot}} = selectForm();
const [{{id}}] = snapshot.fields;
{before};
assert.equal(call("fill", {{[id]: "17"}}), {expected_fill});
assert.equal(quantity.value, {expected_fill} ? "17" : "42");
{after};
assert.equal(call("click"), false);
assert.equal(submitter.clicks ?? 0, 0);
""")


@pytest.mark.parametrize("excluded_attribute", ["disabled", "hidden"])
def test_form_page_omits_select_with_unavailable_current_option(form_page_js, excluded_attribute):
    form_page_js(f"""
const selected = Object.assign(new Element("option", "", "excluded-value"),
    {{label: "Unavailable", index: 0, {excluded_attribute}: true}});
const available = Object.assign(new Element("option", "", "available-value"),
    {{label: "Available", index: 1}});
const select = Object.assign(new Element("select", "select-one", selected.value,
    {{"aria-label": "Status"}}), {{options: [selected, available], selectedIndex: 0}});
form.controls.push(select);
const {{candidate, snapshot}} = selectForm();
assert.deepEqual(candidate.fields.map((field) => field.label), ["Quantity"]);
assert.deepEqual(snapshot.fields.map((field) => field.label), ["Quantity"]);
select.selectedIndex = 1;
select.value = available.value;
assert.deepEqual(selectForm().snapshot.fields[1].options,
    [{{label: "Available", value: "available-value"}}]);
""")


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


def test_pipe_routes_nul_delimited_responses_by_id_and_session(runner):
    class ChunkedReader(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 13))

    messages = [
        {"method": "Target.attachedToTarget"},
        {"id": 1, "sessionId": "different", "result": {}},
        {"id": 1, "sessionId": "page", "result": {"value": "first"}},
        {"id": 2, "result": {"value": "second"}},
    ]
    reader = ChunkedReader(b"".join(json.dumps(message).encode() + b"\0" for message in messages))
    writer = io.BytesIO()
    client = runner.PipeClient(reader, writer)

    async def run():
        assert await client.send("Runtime.evaluate", {"expression": "1"}, session_id="page") == {
            "value": "first"
        }
        assert await client.send("Target.getTargets") == {"value": "second"}

    asyncio.run(run())
    first, second, terminator = writer.getvalue().split(b"\0")
    assert not terminator
    assert json.loads(first) == {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": "1"},
        "sessionId": "page",
    }
    assert json.loads(second) == {"id": 2, "method": "Target.getTargets", "params": {}}


def test_pipe_protocol_error_redacts_browser_message(runner):
    from octop_browser.cdp.client import CDPSessionError

    reader = io.BytesIO(b'{"id":1,"error":{"message":"https://intranet.test/?token=private"}}\0')
    client = runner.PipeClient(reader, io.BytesIO())
    with pytest.raises(CDPSessionError, match=r"^Target.getTargets$"):
        asyncio.run(client.send("Target.getTargets"))


@pytest.mark.parametrize("opaque,truncated", [(True, False), (False, True)])
def test_query_reports_partial_text_without_raw_url(runner, browser, tmp_path, opaque, truncated):
    browser.opaque_frame, browser.truncated = opaque, truncated
    if truncated:
        browser.text = "x" * runner.TEXT_LIMIT
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
    )
    assert replies[1] == {
        "title": "订单",
        "url": "https://intranet.test/orders",
        "text": browser.text,
        "incomplete": True,
    }


def test_query_blocks_an_opaque_frame_without_readable_text(runner, browser, tmp_path):
    browser.opaque_frame = True
    browser.text = ""
    replies = _request(
        runner,
        tmp_path / "profiles",
        {"action": "open"},
        {"action": "query", "tab_id": "A", "url": "https://intranet.test/orders"},
    )
    assert replies[1] == {"error": "BROWSER_READ_BLOCKED"}


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("COLINK_TEST_EDGE") != "1",
    reason="requires COLINK_TEST_EDGE=1 and a native Windows desktop with Edge",
)
def test_edge_private_pipe_and_read_only_evidence(runner, tmp_path, monkeypatch):
    edge = Path(os.environ["PROGRAMFILES(X86)"]) / "Microsoft/Edge/Application/msedge.exe"
    assert edge.is_file()
    monkeypatch.setattr(runner, "browser_executable", lambda: edge)

    class PageHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            path = self.path.partition("?")[0]
            origin = f"http://localhost:{self.server.server_port}"
            pages = {
                "/orders": """
                    <p>Order DEMO-42</p>
                    <div style="opacity:0"><span>hidden opacity</span></div>
                    <div style="display:none"><span>hidden display</span></div>
                    <div style="visibility:hidden"><span>hidden visibility</span>
                        <span style="visibility:visible">visible override</span></div>
                    <div style="content-visibility:hidden"><span>hidden content</span></div>
                    <iframe src="/stock?key=same-origin-secret"></iframe>
                    <div style="opacity:0"><input type="password">
                        <iframe src="/login"></iframe></div>
                    <div id="shadow"><span slot="stock">Reserved 3 units</span>
                        <span>hidden unslotted text</span></div>
                    <script>
                      document.querySelector("#shadow").attachShadow({mode: "open"}).innerHTML =
                        "<p>Warehouse A</p><slot name='stock'></slot>" +
                        "<div style='opacity:0'><input type='password'>hidden shadow text</div>";
                    </script>
                """,
                "/stock": "<p>Available 17 units</p>",
                "/opaque": f'<p>Partial order</p><iframe src="{origin}/stock?key=opaque-secret">',
                "/opaque-only": f'<iframe src="{origin}/stock?key=opaque-secret"></iframe>',
                "/password": '<p>Order DEMO-42</p><iframe src="/login"></iframe>',
                "/shadow-password": """
                    <p>Order DEMO-42</p><div id="shadow"></div>
                    <script>
                      document.querySelector("#shadow").attachShadow({mode: "open"}).innerHTML =
                        "<input type='password'>";
                    </script>
                """,
                "/slot-password": """
                    <div id="shadow"><input slot="login" type="password"></div>
                    <script>
                      document.querySelector("#shadow").attachShadow({mode: "open"}).innerHTML =
                        "<slot name='login'></slot>";
                    </script>
                """,
                "/login": '<input type="password" value="synthetic-password">',
                "/long": "<p>" + ("x" * 60010) + "</p>",
            }
            page = "<!doctype html><title>Example orders</title>" + pages.get(path, "")
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    async def run(origin):
        browser = runner.LocalBrowser(tmp_path.resolve() / "edge-profile")

        async def open_page(path):
            address = origin + path
            result = await browser.action("new_tab", address, "")
            tab_id = result["tab_id"]
            url = address.partition("?")[0]
            async with asyncio.timeout(8):
                while True:
                    tabs = await browser.tabs()
                    if any(tab["id"] == tab_id and tab["url"] == url for tab in tabs):
                        return tab_id, url
                    await asyncio.sleep(0.05)

        try:
            opened = await browser.action("open", "", "")
            assert len(opened["tabs"]) == 1 and opened["tabs"][0]["url"] == ""
            assert not (browser.profile / "DevToolsActivePort").exists()
            process = browser.process
            assert process is not None
            sockets = subprocess.check_output(
                ["netstat.exe", "-ano", "-p", "tcp"], text=True, errors="replace", timeout=8
            )
            assert not any(
                fields[-2:] == ["LISTENING", str(process.pid)]
                for line in sockets.splitlines()
                if (fields := line.split())
            )

            tab_id, url = await open_page("/orders?token=initial")
            result = await browser.action("query", url, tab_id)
            assert all(
                marker in result["text"]
                for marker in (
                    "Order DEMO-42",
                    "visible override",
                    "Available 17 units",
                    "Warehouse A",
                    "Reserved 3 units",
                )
            )
            assert "hidden" not in result["text"]
            assert "secret" not in json.dumps(result)
            assert result["url"] == url and not result["incomplete"]
            captured = await browser.action("capture", url, tab_id)
            assert base64.b64decode(captured["image"]).startswith(b"\x89PNG\r\n\x1a\n")

            client = browser.connection()
            session_id = (
                await client.send("Target.attachToTarget", {"targetId": tab_id, "flatten": True})
            )["sessionId"]
            await client.send(
                "Runtime.evaluate",
                {"expression": "history.replaceState(null, '', '?token=changed')"},
                session_id=session_id,
            )
            await client.send("Target.detachFromTarget", {"sessionId": session_id})
            with pytest.raises(runner.BrowserError, match="BROWSER_READ_BLOCKED"):
                await browser.action("query", url, tab_id)

            tab_id, url = await open_page("/opaque")
            partial = await browser.action("query", url, tab_id)
            assert partial["incomplete"] and partial["text"] == "Partial order"
            assert "opaque-secret" not in json.dumps(partial)
            with pytest.raises(runner.BrowserError, match="BROWSER_READ_BLOCKED"):
                await browser.action("capture", url, tab_id)

            for path in ("/opaque-only", "/password", "/shadow-password", "/slot-password"):
                tab_id, url = await open_page(path)
                for action in ("query", "capture"):
                    with pytest.raises(runner.BrowserError, match="BROWSER_READ_BLOCKED"):
                        await browser.action(action, url, tab_id)

            tab_id, url = await open_page("/long")
            long = await browser.action("query", url, tab_id)
            assert long["incomplete"] and long["text"] == "x" * runner.TEXT_LIMIT
        finally:
            await browser.close()
        assert process.poll() is not None

    with ThreadingHTTPServer(("127.0.0.1", 0), PageHandler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            asyncio.run(run(f"http://127.0.0.1:{server.server_port}"))
        finally:
            server.shutdown()
            thread.join()


def test_prepared_upload_is_single_use_and_bound_to_the_file_input(runner, browser, tmp_path):
    upload = tmp_path / "sample.txt"
    upload.write_text("sample")
    action = {"tab_id": "A", "url": "https://intranet.test/orders", "selector": "#attachment"}
    prepare = {"action": "prepare_upload", **action}
    perform = {"action": "upload", "file_path": str(upload), **action}
    profile = tmp_path / "profile"
    replies = _request(runner, profile, {"action": "open"}, prepare, perform, perform)
    assert replies[1]["target"] == "#attachment"
    assert replies[2]["file"] == "sample.txt"
    assert replies[3] == {"error": "BROWSER_ACTION_CHANGED"}
    assert browser.uploaded_files == [str(upload)]
    assert sum(method == "DOM.setFileInputFiles" for method, _ in browser.commands) == 1

    invalid = _request(runner, profile, {**perform, "file_path": "relative.txt"})
    assert invalid == [{"error": "BROWSER_INVALID_REQUEST"}]


def test_download_only_accepts_the_completed_file_for_its_frame_and_link(runner, browser, tmp_path):
    destination = tmp_path / "downloads"
    destination.mkdir()
    guid = "84564e8c-6b21-4b63-8be7-a486401f0906"
    actual = destination / guid
    unrelated = destination / "a-unrelated.txt"
    browser.target_url = "https://intranet.test/export"
    event_url = "blob:https://intranet.test/approved"

    def finish_download():
        unrelated.write_bytes(b"unrelated")
        actual.write_bytes(b"actual")
        events = browser.client.download_events
        events.append(
            {
                "method": "Browser.downloadWillBegin",
                "params": {"guid": guid, "frameId": "frame", "url": event_url},
            }
        )
        events.append(
            {
                "method": "Browser.downloadProgress",
                "params": {"guid": guid, "state": "completed", "receivedBytes": 6},
            }
        )

    browser.on_evaluate = finish_download
    params = {
        "tab_id": "A",
        "url": "https://intranet.test/orders",
        "download_url": browser.target_url,
    }
    requests = (
        {"action": "open"},
        {"action": "prepare_download", **params},
        {"action": "download", "download_path": str(destination), **params},
    )
    result = _request(runner, tmp_path / "profile", *requests)[-1]
    assert result == {"file": guid, "size": 6, "url": "https://intranet.test/orders"}
    assert browser.last_allowed_download["behavior"] == "allowAndName"
    assert browser.download_behavior == {"behavior": "deny"}

    event_url = "blob:https://intranet.test/unrelated"
    result = _request(runner, tmp_path / "profile", *requests)[-1]
    assert result == {"error": "BROWSER_TRANSFER_UNCERTAIN"}


def test_download_requires_exactly_one_source_and_prepared_action(runner, browser, tmp_path):
    base = "https://intranet.test/orders"
    download = {
        "action": "download",
        "url": base,
        "tab_id": "A",
        "download_path": str(tmp_path),
    }
    requests = [
        download,
        {**download, "download_url": base, "selector": "#download"},
    ]
    replies = _request(runner, tmp_path / "profile", *requests)
    assert replies == [{"error": "BROWSER_INVALID_REQUEST"}] * 2
    assert _request(
        runner,
        tmp_path / "profile",
        {"action": "open"},
        {"action": "list"},
        {**download, "download_url": base},
    )[-1] == {"error": "BROWSER_ACTION_CHANGED"}
