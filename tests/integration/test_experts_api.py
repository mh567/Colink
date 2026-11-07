"""tests/integration/test_experts_api.py — expert catalog endpoints.

Covers list / detail / one-click create. Uses the bundled library that
ships with the package (``src/octop/experts/library/``) so the data
shape matches production exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


async def test_patch_expert_rejects_private_package_in_nested_config(env_boundary: Any) -> None:
    client, server, _admin_auth, _alice_auth, bob_auth, ctx = env_boundary
    package = server.services.skill_package_repo.create(
        id="alice-private", name="Alice private", created_by=str(ctx["alice_id"])
    )
    created = await client.post("/api/agents", headers=bob_auth, json={"name": "Bob expert"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    denied = await client.patch(
        f"/api/agents/{agent_id}",
        headers=bob_auth,
        json={"name": "Changed", "config": {"skill_package_ids": [package.id]}},
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["error"]["code"] == "SKILL_PACKAGE_NOT_FOUND"
    detail = await client.get(f"/api/agents/{agent_id}", headers=bob_auth)
    assert detail.json()["name"] == "Bob expert"
    assert detail.json()["skill_package_ids"] == []


@pytest.mark.parametrize("operation", ["create", "patch"])
@pytest.mark.parametrize(
    ("field", "status", "code"),
    [
        ("skill_package_ids", 404, "SKILL_PACKAGE_NOT_FOUND"),
        ("knowledge_base_ids", 404, "KNOWLEDGE_NOT_FOUND"),
        ("mcp_servers", 400, "CONNECTOR_NOT_BOUND"),
    ],
)
async def test_expert_nested_resources_reject_unknown_ids(
    env: Any, operation: str, field: str, status: int, code: str
) -> None:
    client, _server, auth = env
    before = (await client.get("/api/agents", headers=auth)).json()
    body = {"name": "Invalid resources", "config": {field: ["missing-resource"]}}
    if operation == "create":
        response = await client.post("/api/agents", headers=auth, json=body)
    else:
        response = await client.patch(
            f"/api/agents/{before[0]['agent_id']}", headers=auth, json=body
        )
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    after = (await client.get("/api/agents", headers=auth)).json()
    assert [(row["agent_id"], row["name"], row[field]) for row in after] == [
        (row["agent_id"], row["name"], row[field]) for row in before
    ]


@pytest.mark.parametrize("operation", ["create", "patch"])
async def test_admin_cannot_attach_employee_private_skill_package(
    env_boundary: Any, operation: str
) -> None:
    client, server, admin_auth, _alice_auth, _bob_auth, ctx = env_boundary
    package = server.services.skill_package_repo.create(
        id="private-employee-kit", name="Employee kit", created_by=str(ctx["alice_id"])
    )
    body = {"name": "Admin expert", "config": {"skill_package_ids": [package.id]}}
    if operation == "create":
        response = await client.post("/api/agents", headers=admin_auth, json=body)
    else:
        response = await client.patch(
            f"/api/agents/{ctx['alice_agent_id']}", headers=admin_auth, json=body
        )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "SKILL_PACKAGE_NOT_FOUND"


@pytest.mark.parametrize("field", ["knowledge_base_ids", "mcp_servers"])
async def test_nested_expert_resource_selection_uses_owner_authorization(
    env_boundary: Any, field: str
) -> None:
    client, server, _admin_auth, alice_auth, bob_auth, ctx = env_boundary
    if field == "knowledge_base_ids":
        resource = server.services.knowledge_repo.create_base(
            owner_user_id=ctx["alice_id"], name="Private documents"
        )
        resource_id = resource.id
        expected_status = 404
    else:
        connector = await client.post(
            "/api/connector-instances",
            headers=alice_auth,
            json={
                "kind": "tencent-docs",
                "display_name": "Private docs connector",
                "credentials": {"token": "test-token"},
            },
        )
        assert connector.status_code == 201, connector.text
        resource_id = connector.json()["mcp_server_name"]
        expected_status = 400
    body = {"name": "Selected resources", "config": {field: [resource_id]}}
    denied = await client.post("/api/agents", headers=bob_auth, json=body)
    assert denied.status_code == expected_status, denied.text
    own = await client.post("/api/agents", headers=alice_auth, json=body)
    assert own.status_code == 201, own.text
    assert own.json()[field] == [resource_id]

    other = await client.post("/api/agents", headers=bob_auth, json={"name": "Bob resource test"})
    assert other.status_code == 201, other.text
    agent_id = other.json()["agent_id"]
    denied = await client.patch(f"/api/agents/{agent_id}", headers=bob_auth, json=body)
    assert denied.status_code == expected_status, denied.text
    detail = await client.get(f"/api/agents/{agent_id}", headers=bob_auth)
    assert detail.json()[field] == []
    assert detail.json()["name"] == "Bob resource test"


async def test_local_expert_cannot_start_server_runtime_or_drop_local_boundary(
    env_with_provider: Any,
) -> None:
    client, server, auth = env_with_provider
    server.services.user_repo.set_permissions(1, ["local_docx"])
    created = await client.post(
        "/api/experts/local",
        headers=auth,
        json={
            "name": "Local expert",
            "default_model": "openai/gpt-4o",
            "actions": ["local_docx"],
        },
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    detail = await client.get(f"/api/agents/{agent_id}", headers=auth)
    assert detail.json()["state"] == "stopped"
    shared = await client.post(
        "/api/agents",
        headers=auth,
        json={
            "name": "Shared local expert",
            "is_shared": True,
            "config": {"local_only": True, "local_actions": ["local_docx"]},
        },
    )
    assert shared.status_code == 403, shared.text
    started = await client.post(f"/api/agents/{agent_id}/start", headers=auth)
    assert started.status_code == 403, started.text
    removed = await client.patch(
        f"/api/agents/{agent_id}", headers=auth, json={"config": {"local_only": False}}
    )
    assert removed.status_code == 403, removed.text
    omitted = await client.patch(f"/api/agents/{agent_id}", headers=auth, json={"config": {}})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["config"]["local_only"] is True
    assert omitted.json()["config"]["local_actions"] == ["local_docx"]
    expanded = await client.patch(
        f"/api/agents/{agent_id}",
        headers=auth,
        json={"config": {"local_actions": ["local_docx", "local_xlsx"]}},
    )
    assert expanded.status_code == 403, expanded.text


async def test_local_expert_requires_explicit_action_grants_even_for_admin(
    env_with_provider: Any,
) -> None:
    client, _server, auth = env_with_provider
    response = await client.post(
        "/api/experts/local",
        headers=auth,
        json={
            "name": "Unapproved action",
            "default_model": "openai/gpt-4o",
            "actions": ["local_docx"],
        },
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("endpoint", ["download", "doc"])
async def test_local_expert_cannot_read_server_files_via_workspace(
    env_with_provider: Any, tmp_path: Path, endpoint: str
) -> None:
    from docx import Document

    client, server, auth = env_with_provider
    server.services.user_repo.set_permissions(1, ["local_docx"])
    created = await client.post(
        "/api/experts/local",
        headers=auth,
        json={"name": "Local files", "default_model": "openai/gpt-4o", "actions": ["local_docx"]},
    )
    assert created.status_code == 201, created.text
    server_file = tmp_path / "server-private.docx"
    document = Document()
    document.add_paragraph("Private server content must not leave the server")
    document.save(server_file)

    response = await client.get(
        f"/api/agents/{created.json()['agent_id']}/workspace/{endpoint}",
        headers=auth,
        params={"path": str(server_file), "from_workspace": "false"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("actions", [[], ["execute"], [{}], "local_docx"])
async def test_local_expert_rejects_invalid_action_selection(
    env_with_provider: Any, actions: Any
) -> None:
    client, server, auth = env_with_provider
    server.services.user_repo.set_permissions(1, ["local_docx"])
    response = await client.post(
        "/api/experts/local",
        headers=auth,
        json={
            "name": "Invalid local expert",
            "default_model": "openai/gpt-4o",
            "actions": actions,
        },
    )
    assert response.status_code == 422, response.text


async def test_list_experts_hides_blank_template(env: Any) -> None:
    c, _srv, auth = env
    r = await c.get("/api/experts", headers=auth)
    assert r.status_code == 200
    rows = r.json()
    ids = {row["id"] for row in rows}
    # Product templates live in the expert market. The blank ``default`` scaffold
    # stays on disk for one-click create, and the list API hides it.
    assert "default" not in ids
    assert "general-assistant" not in ids


async def test_get_expert_includes_prompt_files(env: Any) -> None:
    c, _srv, auth = env
    r = await c.get("/api/experts/default", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "default"
    assert body["files"] == ["AGENTS.md"]
    assert body["prompt_files"] == ["AGENTS.md"]


async def test_get_unknown_expert_404(env: Any) -> None:
    c, _srv, auth = env
    r = await c.get("/api/experts/this-id-does-not-exist", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_create_agent_from_expert(env: Any) -> None:
    """One-click create from /api/agents/from-expert/{id}.

    The new agent should:
      - exist in /api/agents
      - not copy expert markdown into ``system_prompt`` (persona lives in workspace)
      - be in a non-running state (autostart=False; no provider)
    """
    c, _srv, auth = env

    r = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "default-bot"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expert_id"] == "default"
    assert body.get("user_id") == 1
    assert body["state"] in {"running", "failed", "stopped", "unknown", "starting"}
    new_id = body["agent_id"]

    # Listed under /api/agents
    rows = (await c.get("/api/agents", headers=auth)).json()
    created = next(a for a in rows if a["agent_id"] == new_id and a["name"] == "default-bot")
    assert created["user_id"] == 1
    assert any(a["agent_id"] == new_id and a["name"] == "default-bot" for a in rows)

    detail_row = (await c.get(f"/api/agents/{new_id}", headers=auth)).json()
    assert detail_row["name"] == "default-bot"
    assert detail_row.get("system_prompt") in (None, "")


async def test_create_agent_from_expert_applies_file_overrides(env: Any) -> None:
    c, srv, auth = env
    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "prompt-edited-bot",
            "file_overrides": [{"name": "AGENTS.md", "content": "custom agents guide"}],
            "omit_files": ["skills/does-not-exist"],
        },
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    workspace = srv.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        pytest.skip("workspace not available before bootstrap")
    text = await workspace.aread_text("AGENTS.md")
    assert text == "custom agents guide"


async def test_create_agent_from_expert_applies_quick_prompts(env: Any) -> None:
    c, srv, auth = env
    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "page-config-bot",
            "quick_prompts": [
                {
                    "title": {"zh": "创建卡", "en": "Create card"},
                    "description": {"zh": "描述", "en": "Desc"},
                    "prompt": {"zh": "请开始", "en": "Start"},
                    "color": "#fff7ed",
                    "icon_name": "zap",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    workspace = srv.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        pytest.skip("workspace not available before bootstrap")
    from octop.infra.agents.experts.catalog import (
        WORKSPACE_MANIFEST_PATH,
        read_workspace_manifest_data,
    )

    data = await read_workspace_manifest_data(workspace)
    assert data is not None
    assert data.get("quick_prompts")
    assert data["quick_prompts"][0]["title"]["zh"] == "创建卡"
    # Seeded template keys must survive a page-config overlay.
    text = await workspace.aread_text(WORKSPACE_MANIFEST_PATH)
    assert text is not None
    assert "创建卡" in text


async def test_create_agent_from_expert_copies_full_skill_dir(env: Any) -> None:
    c, srv, auth = env
    source = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "copy-skill-src"},
    )
    assert source.status_code == 201, source.text
    src_id = source.json()["agent_id"]
    src_ws = srv.app_runtime.agent_registry.workspace_for_agent(src_id)
    if src_ws is None:
        pytest.skip("workspace not available before bootstrap")
    await src_ws.aupload_many(
        [
            ("skills/pack-demo/SKILL.md", b"---\nname: pack-demo\n---\n# Pack\n"),
            ("skills/pack-demo/refs/note.md", b"keep me"),
        ]
    )

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "copy-skill-dest",
            "copy_skills": [{"agent_id": src_id, "slug": "pack-demo"}],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json().get("copy_skill_errors") in (None, [])
    dest_ws = srv.app_runtime.agent_registry.workspace_for_agent(created.json()["agent_id"])
    if dest_ws is None:
        pytest.skip("workspace not available before bootstrap")
    assert await dest_ws.aread_text("skills/pack-demo/refs/note.md") == "keep me"


async def test_create_agent_from_expert_rejects_unsafe_override_path(env: Any) -> None:
    c, _srv, auth = env
    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "unsafe-override-bot",
            "file_overrides": [{"name": "../secret", "content": "nope"}],
        },
    )
    assert created.status_code == 400, created.text


async def test_create_agent_from_expert_duplicate_name_409(env: Any) -> None:
    c, _srv, auth = env
    first = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "same-name-bot"},
    )
    assert first.status_code == 201, first.text

    second = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "same-name-bot"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "AGENT_NAME_TAKEN"


async def test_create_agent_from_unknown_expert_404(env: Any) -> None:
    c, _srv, auth = env
    r = await c.post(
        "/api/agents/from-expert/no-such-thing",
        headers=auth,
        json={"name": "foo"},
    )
    assert r.status_code == 404


async def test_create_agent_from_expert_with_provider_config(env: Any) -> None:
    """Optional providers land in config; default_model is stored on the agent row."""
    c, _srv, auth = env
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "configured-default",
            "providers": ["my-openai"],
            "default_model": "openai:gpt-4o-mini",
        },
    )
    assert r.status_code == 201


async def test_create_agent_from_expert_mounts_skill_packages(env: Any) -> None:
    c, _srv, auth = env
    package_ids = [
        (
            await c.post(
                "/api/skill-packages",
                headers=auth,
                json={"name": name},
            )
        ).json()["id"]
        for name in ("Office", "Engineering")
    ]

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "packaged-expert", "skill_package_ids": package_ids},
    )

    assert created.status_code == 201, created.text
    mounted = await c.get(
        f"/api/agents/{created.json()['agent_id']}/skill-packages",
        headers=auth,
    )
    assert mounted.status_code == 200, mounted.text
    assert mounted.json()["package_ids"] == package_ids
    assert [package["id"] for package in mounted.json()["packages"]] == package_ids


async def test_create_agent_from_expert_rejects_unknown_package_before_creation(env: Any) -> None:
    c, _server, auth = env
    agents_before = (await c.get("/api/agents", headers=auth)).json()

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "missing-package-expert", "skill_package_ids": ["MISSING"]},
    )

    assert created.status_code == 404, created.text
    assert created.json()["error"]["code"] == "SKILL_PACKAGE_NOT_FOUND"
    agents_after = (await c.get("/api/agents", headers=auth)).json()
    assert {agent["agent_id"] for agent in agents_after} == {
        agent["agent_id"] for agent in agents_before
    }


@pytest.mark.parametrize("package_id", ["existing", "MISSING"])
async def test_hub_install_cannot_bypass_offline_review(
    env: Any, monkeypatch: pytest.MonkeyPatch, package_id: str
) -> None:
    from octop.api.routers import experts as experts_router

    client, _server, auth = env
    if package_id == "existing":
        created = await client.post(
            "/api/skill-packages", headers=auth, json={"name": "Offline only"}
        )
        package_id = created.json()["id"]

    async def unexpected_market(**_kwargs: Any) -> Any:
        raise AssertionError("disabled market must not create or download")

    monkeypatch.setattr(experts_router, "create_skillhub_market_agent", unexpected_market)
    response = await client.post(
        "/api/experts/hub/hub-expert/install",
        headers=auth,
        json={"skill_package_ids": [package_id]},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "LOCAL_EXPERT_REVIEW_REQUIRED"


async def test_get_expert_includes_file_contents(env: Any) -> None:
    c, _srv, auth = env
    r = await c.get("/api/experts/default", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "file_contents" in body
    assert isinstance(body["file_contents"], list)
    names = {item["name"] for item in body["file_contents"]}
    assert names == {"AGENTS.md"}
    first = body["file_contents"][0]
    assert "name" in first and "content" in first
    assert isinstance(first["content"], str) and first["content"]


async def test_get_expert_file_contents_limited_to_preview_paths(env: Any) -> None:
    c, _srv, auth = env
    r = await c.get("/api/experts/general-assistant", headers=auth)
    assert r.status_code == 200
    body = r.json()
    names = {item["name"] for item in body["file_contents"]}
    assert names == set(body["prompt_files"]) | {
        f for f in body["files"] if f.startswith("skills/")
    }
    assert "skills/stock-info/SKILL.md" in names
    assert "manifest.json" not in names


async def test_create_from_expert_stores_icon_color(env: Any) -> None:
    c, _srv, auth = env
    expert = (await c.get("/api/experts/default", headers=auth)).json()
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "icon-color-agent"},
    )
    assert r.status_code == 201
    agent_id = r.json()["id"]
    rows = (await c.get("/api/agents", headers=auth)).json()
    agent = next(a for a in rows if a["id"] == agent_id)
    assert agent["icon_name"] == expert.get("icon_name")
    assert agent["color"] == expert.get("color")


async def test_create_from_expert_stores_default_model(env: Any) -> None:
    c, _srv, auth = env
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "model-agent", "default_model": "openai/gpt-4o"},
    )
    assert r.status_code == 201
    agent_id = r.json()["id"]
    rows = (await c.get("/api/agents", headers=auth)).json()
    agent = next(a for a in rows if a["id"] == agent_id)
    assert agent["default_model"] == "openai/gpt-4o"


async def test_create_from_expert_default_name_uses_expert_label(env: Any) -> None:
    """Omitting ``name`` should use the expert template's localized label."""
    c, _srv, auth = env
    r = await c.post(
        "/api/agents/from-expert/default",
        headers={**auth, "Accept-Language": "zh"},
        json={},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "通用专家"


async def test_create_from_expert_stores_backend(env: Any) -> None:
    c, _srv, auth = env
    backend_spec = {"type": "local_shell", "virtual_mode": True, "root_dir": "/"}
    r = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "backend-agent", "backend": backend_spec},
    )
    assert r.status_code == 201
    agent_id = r.json()["id"]
    rows = (await c.get("/api/agents", headers=auth)).json()
    agent = next(a for a in rows if a["id"] == agent_id)
    assert agent["config"].get("backend") == backend_spec


async def test_create_agent_from_expert_stores_composer_defaults(env: Any) -> None:
    c, server, auth = env
    kb = server.services.knowledge_repo.create_base(owner_user_id=1, name="Policies")
    connector = await c.post(
        "/api/connector-instances",
        headers=auth,
        json={
            "kind": "tencent-docs",
            "display_name": "Docs",
            "credentials": {"token": "test-token"},
        },
    )
    assert connector.status_code == 201, connector.text
    mcp_name = connector.json()["mcp_server_name"]

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={
            "name": "composer-defaults-expert",
            "knowledge_base_ids": [kb.id],
            "mcp_servers": [mcp_name],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    detail = (await c.get(f"/api/agents/{body['agent_id']}", headers=auth)).json()
    assert detail["knowledge_base_ids"] == [kb.id]
    assert detail["mcp_servers"] == [mcp_name]
    assert "knowledge_base_ids" not in (detail.get("config") or {})
    assert "mcp_servers" not in (detail.get("config") or {})


async def test_create_agent_from_expert_rejects_unknown_knowledge_base(env: Any) -> None:
    c, _server, auth = env
    agents_before = (await c.get("/api/agents", headers=auth)).json()

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "missing-kb-expert", "knowledge_base_ids": ["MISSING"]},
    )

    assert created.status_code == 404, created.text
    assert created.json()["error"]["code"] == "KNOWLEDGE_NOT_FOUND"
    agents_after = (await c.get("/api/agents", headers=auth)).json()
    assert {agent["agent_id"] for agent in agents_after} == {
        agent["agent_id"] for agent in agents_before
    }


async def test_create_agent_from_expert_rejects_unknown_connector(env: Any) -> None:
    c, _server, auth = env
    agents_before = (await c.get("/api/agents", headers=auth)).json()

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "missing-connector-expert", "mcp_servers": ["unknown__instance"]},
    )

    assert created.status_code == 400, created.text
    assert created.json()["error"]["code"] == "CONNECTOR_NOT_BOUND"
    agents_after = (await c.get("/api/agents", headers=auth)).json()
    assert {agent["agent_id"] for agent in agents_after} == {
        agent["agent_id"] for agent in agents_before
    }


async def test_patch_agent_composer_defaults(env: Any) -> None:
    c, server, auth = env
    kb = server.services.knowledge_repo.create_base(owner_user_id=1, name="Handbook")
    connector = await c.post(
        "/api/connector-instances",
        headers=auth,
        json={
            "kind": "tencent-docs",
            "display_name": "Wiki",
            "credentials": {"token": "test-token"},
        },
    )
    assert connector.status_code == 201, connector.text
    mcp_name = connector.json()["mcp_server_name"]

    created = await c.post(
        "/api/agents/from-expert/default",
        headers=auth,
        json={"name": "patch-composer-defaults"},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    detail = (await c.get(f"/api/agents/{agent_id}", headers=auth)).json()
    assert detail["knowledge_base_ids"] == []
    assert detail["mcp_servers"] == []

    patched = await c.patch(
        f"/api/agents/{agent_id}",
        headers=auth,
        json={"knowledge_base_ids": [kb.id], "mcp_servers": [mcp_name]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["knowledge_base_ids"] == [kb.id]
    assert patched.json()["mcp_servers"] == [mcp_name]

    cleared = await c.patch(
        f"/api/agents/{agent_id}",
        headers=auth,
        json={"knowledge_base_ids": [], "mcp_servers": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["knowledge_base_ids"] == []
    assert cleared.json()["mcp_servers"] == []
