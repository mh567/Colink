"""Tests for [T01] Component upgrade and hygiene verification (Issue #2).

Verifies:
1. octop-harness == 1.0.4, octop-memory == 1.0.2 (locked), octop-browser == 1.0.0.
2. Delivery packages exclude restricted PowerPoint (PPT) skill materials.
3. Workspace builtin skills sync prunes restricted PowerPoint skill materials.
4. Minimal local task completes successfully with model calling, tool execution,
   and checkpoint history under upgraded components.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import sys
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from octop.infra.agents.builtin_skills import (
    OCTOP_BUILTIN_SKILLS_ROOT,
    RETIRED_BUILTIN_SKILLS,
    sync_octop_builtin_skills,
)
from octop.infra.company_runtime.client import RemoteCheckpointSaver
from octop.infra.company_runtime.runtime import EnterpriseRuntime


def test_component_versions_match_acceptance_criteria() -> None:
    harness_ver = importlib.metadata.version("octop-harness")
    memory_ver = importlib.metadata.version("octop-memory")
    browser_ver = importlib.metadata.version("octop-browser")

    assert harness_ver == "1.0.4", f"Expected octop-harness 1.0.4, got {harness_ver}"
    assert memory_ver == "1.0.2", f"Expected octop-memory 1.0.2, got {memory_ver}"
    assert browser_ver == "1.0.0", f"Expected octop-browser 1.0.0, got {browser_ver}"

    root = Path(__file__).resolve().parents[3]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert "octop-harness[all]==1.0.4" in deps
    assert "octop-memory>=1.0.2" in deps
    assert "octop-browser>=1.0.0" in deps


def test_retired_builtin_skills_includes_powerpoint() -> None:
    assert "powerpoint" in RETIRED_BUILTIN_SKILLS


@pytest.mark.asyncio
async def test_workspace_sync_prunes_restricted_powerpoint_skill(tmp_path: Path) -> None:
    deleted_paths: list[str] = []
    existing_paths = {
        f"{OCTOP_BUILTIN_SKILLS_ROOT}/powerpoint",
        f".octop/{OCTOP_BUILTIN_SKILLS_ROOT}/powerpoint",
    }

    class FakeWorkspace:
        def __init__(self) -> None:
            self.backend = SimpleNamespace(root_dir=str(tmp_path), virtual_mode=False)
            self.workspace_dir = tmp_path

        async def amkdir(self, path: str) -> None:
            pass

        def system_rel(self, path: str) -> str:
            return f".octop/{path}"

        async def aexists(self, path: str) -> bool:
            return path in existing_paths

        async def adelete(self, path: str) -> None:
            deleted_paths.append(path)
            existing_paths.discard(path)

        async def aupload_many(self, uploads: list[tuple[str, bytes]]) -> None:
            pass

    ws = FakeWorkspace()
    await sync_octop_builtin_skills(ws)

    assert f"{OCTOP_BUILTIN_SKILLS_ROOT}/powerpoint" in deleted_paths
    assert f".octop/{OCTOP_BUILTIN_SKILLS_ROOT}/powerpoint" in deleted_paths
    assert not existing_paths


def test_offline_package_runtime_excludes_restricted_powerpoint_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    package_path = Path(__file__).resolve().parents[3] / "desktop/package-company.py"
    spec = importlib.util.spec_from_file_location("package_company", package_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "source"
    packages = source / "packages"
    packages.mkdir(parents=True)
    (source / "launch.py").write_text("def _bootstrap(): pass\n# company-runtime\n")
    (packages / "octop").mkdir()
    (packages / "octop/company_runtime.py").write_text("# company runtime entry\n")
    (packages / "octop-1.0.0.dist-info").mkdir()
    (packages / "octop-1.0.0.dist-info/METADATA").write_text(
        "Metadata-Version: 2.1\nName: octop\nVersion: 1.0.0\n"
    )
    (packages / "octop_harness").mkdir()
    (packages / "octop_harness/__init__.py").write_text("")

    # Simulated powerpoint skill with restricted materials
    ppt_en = packages / "octop_harness/builtin/skills/en/powerpoint"
    ppt_en.mkdir(parents=True)
    (ppt_en / "SKILL.md").write_text("name: powerpoint\n")
    (ppt_en / "LICENSE.txt").write_text("Anthropic restricted license\n")

    metadata = packages / "octop_harness-1.0.4.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Metadata-Version: 2.1\nName: octop-harness\nVersion: 1.0.4\n")

    for mod in module.RUNTIME_MODULES:
        (packages / mod).mkdir()
        (packages / mod / "__init__.py").write_text("")

    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_text("")
    tools = source / "tools"
    tools.mkdir()
    (tools / "ffmpeg.exe").write_text("")
    (tools / "ffprobe.exe").write_text("")

    monkeypatch.setattr(module, "native_binary_target", lambda path: ("windows", "amd64"))
    monkeypatch.setattr(module, "python_source_digest", lambda path: "mock-digest")
    monkeypatch.setattr(
        module,
        "release_identity",
        lambda: {
            "octop_version": "1.0.0",
            "source_commit": "abcdef",
            "source_dirty": False,
            "python_source_sha256": "mock-digest",
            "device_protocol_version": 1,
            "company_protocol_version": 1,
        },
    )

    dest = tmp_path / "output.zip"
    module.package_runtime(source, dest, "windows", "amd64")

    assert dest.is_file()
    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        ppt_files = [n for n in names if "powerpoint" in n.lower()]
        assert not ppt_files, f"Restricted PowerPoint files leaked into package: {ppt_files}"


class LocalTaskModel:
    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            yield AIMessageChunk(
                content="Task completed: verified local output.",
                id="m2",
                usage_metadata={"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
            )
        else:
            yield AIMessageChunk(
                content="",
                id="m1",
                tool_call_chunks=[
                    {
                        "name": "write_file",
                        "args": json.dumps(
                            {"file_path": "/result.txt", "content": "colink-t01-verified"}
                        ),
                        "id": "write_1",
                        "index": 0,
                    }
                ],
            )


@pytest.mark.asyncio
async def test_minimal_task_execution_with_model_tool_checkpoint(company, tmp_path: Path) -> None:
    server, client, _, _ = company

    agent = SimpleNamespace(
        workspace=SimpleNamespace(aread_text=AsyncMock(return_value=None)),
        list_skill_summaries=AsyncMock(return_value=[]),
        model_factory=SimpleNamespace(
            list_refs=lambda: ["test/model"], get_chat_model=lambda ref: LocalTaskModel()
        ),
        config=SimpleNamespace(pick_default_model_ref=lambda: "test/model"),
        memory=None,
    )
    server.app_runtime = SimpleNamespace(
        agent_registry=SimpleNamespace(get_agent=lambda aid: agent),
        gateway=SimpleNamespace(ws_hub=SimpleNamespace(is_turn_active=lambda thread: False)),
    )

    workspace = tmp_path / "employee_workspace"
    workspace.mkdir()
    events: list[dict] = []
    runtime = EnterpriseRuntime(events.append)
    runtime.client = client
    runtime.workspace = workspace
    runtime.state = tmp_path / "state"
    runtime.authorized = True
    runtime.installation_token = "i" * 32
    runtime.tools = {
        "ffmpeg": "test",
        "openpyxl": "test",
        "python-docx": "test",
        "Pillow": "test",
    }
    runtime.tool_path = str(Path(sys.executable).parent)

    try:
        await runtime.start(
            {
                "task_id": "task_t01",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Generate result.txt with verification token",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 30)

        assert runtime.tasks["thread1"]["status"] == "completed"
        output_file = workspace / "thread1" / "result.txt"
        assert output_file.is_file()
        assert output_file.read_text(encoding="utf-8") == "colink-t01-verified"

        saver = RemoteCheckpointSaver(client, "task_t01", "thread1")
        cp = await saver.aget_tuple({"configurable": {"thread_id": "thread1"}})
        assert cp is not None
        assert server.services.thread_message_repo.head("thread1") >= 4
    finally:
        await runtime.close()
