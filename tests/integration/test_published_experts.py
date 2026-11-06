"""Approved offline editions replace owner-published workspace snapshots.

Workspace snapshot serialization retains its unit coverage; HTTP callers may no
longer publish or install those snapshots to bypass administrator review.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.users.groups import RESOURCE_MODEL, RESOURCE_SKILL_PACKAGE, GroupAccess
from tests.support.auth import create_user, seed_openai_provider

ROOT = "/api/experts/local"


def _package(version: str = "1.0", **changes: Any) -> dict[str, Any]:
    return {
        "name": "Report helper",
        "description": "Approved report instructions",
        "version": version,
        "source": "Enterprise editorial team",
        "license": "Internal redistribution permitted",
        "system_prompt": f"Approved instructions {version}",
        "actions": ["local_docx"],
        "skills": [],
        **changes,
    }


def _config(**changes: Any) -> dict[str, Any]:
    return {
        "name": "Private installed expert",
        "description": "My report helper",
        "default_model": "openai/gpt-4o",
        "actions": ["local_docx"],
        "system_prompt": "Untrusted installer replacement",
        "skill_package_ids": [],
        "knowledge_base_ids": [],
        **changes,
    }


async def _publish(client: Any, admin: dict[str, str], **changes: Any) -> dict[str, Any]:
    response = await client.post(
        f"{ROOT}/releases",
        headers=admin,
        json={"package": _package(**changes), "reviewed": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _users(env: tuple[Any, Any, dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    client, _server, admin = env
    await seed_openai_provider(client, admin)
    owner = await create_user(
        client, admin, username="publish_owner", permissions=["experts", "local_docx"]
    )
    peer = await create_user(
        client, admin, username="publish_peer", permissions=["experts", "local_docx"]
    )
    return owner, peer


@pytest.mark.parametrize("actor", ["owner", "admin"])
async def test_legacy_publish_refresh_and_install_cannot_bypass_review(env, actor):
    client, _server, admin = env
    owner, _peer = await _users(env)
    created = await client.post(ROOT, headers=owner, json=_config())
    assert created.status_code == 201, created.text
    edition = await _publish(client, admin)
    headers = owner if actor == "owner" else admin
    for path, body in (
        (f"/api/agents/{created.json()['agent_id']}/publish-expert", {"name": "Bypass"}),
        (f"/api/experts/published/{edition['id']}/refresh", {"name": "Mutated edition"}),
        (f"/api/experts/published/{edition['id']}/install", {"name": "Uncontrolled runtime"}),
    ):
        denied = await client.post(path, headers=headers, json=body)
        assert denied.status_code == 403, denied.text
    listed = await client.get(f"{ROOT}/releases", headers=owner)
    assert listed.json() == [edition]


@pytest.mark.parametrize("actor", ["employee", "admin"])
async def test_public_market_cannot_bypass_offline_review(env, actor):
    client, _server, admin = env
    employee = await create_user(client, admin, username="market_employee")
    headers = employee if actor == "employee" else admin
    for method, path in (
        ("GET", "/api/experts/hub"),
        ("GET", "/api/experts/hub/remote-template"),
        ("POST", "/api/experts/hub/remote-template/install"),
    ):
        denied = await client.request(
            method, path, headers=headers, json={"name": "Remote template"}
        )
        assert denied.status_code == 403, denied.text


async def test_admin_unpublish_preserves_installed_private_fork(env):
    client, _server, admin = env
    owner, peer = await _users(env)
    edition = await _publish(client, admin)
    installed = await client.post(
        f"{ROOT}/releases/{edition['id']}/install", headers=peer, json=_config()
    )
    assert installed.status_code == 201, installed.text
    fork = installed.json()
    assert fork["published_expert_id"] == edition["id"]
    assert fork["system_prompt"] == edition["system_prompt"]
    for employee in (owner, peer):
        denied = await client.delete(f"/api/experts/published/{edition['id']}", headers=employee)
        assert denied.status_code == 403, denied.text
    removed = await client.delete(f"/api/experts/published/{edition['id']}", headers=admin)
    assert removed.status_code == 204, removed.text
    assert (await client.get(f"{ROOT}/releases", headers=peer)).json() == []
    own = await client.get(ROOT, headers=peer)
    assert own.status_code == 200
    assert own.json()[0]["agent_id"] == fork["agent_id"]
    assert own.json()[0]["system_prompt"] == edition["system_prompt"]
    for other in (owner, admin):
        assert (await client.get(ROOT, headers=other)).json() == []
        denied = await client.get(f"/api/agents/{fork['agent_id']}", headers=other)
        assert denied.status_code == 403, denied.text


async def test_new_edition_keeps_old_version_and_existing_installs(env):
    client, _server, admin = env
    owner, peer = await _users(env)
    first = await _publish(client, admin)
    installed = await client.post(
        f"{ROOT}/releases/{first['id']}/install", headers=owner, json=_config()
    )
    assert installed.status_code == 201, installed.text
    second = await _publish(client, admin, version="2.0")
    editions = (await client.get(f"{ROOT}/releases", headers=peer)).json()
    assert {item["version"] for item in editions} == {"1.0", "2.0"}
    for edition in (first, second):
        response = await client.post(
            f"{ROOT}/releases/{edition['id']}/install",
            headers=peer,
            json=_config(name=f"Version {edition['version']}"),
        )
        assert response.status_code == 201, response.text
        assert response.json()["system_prompt"] == edition["system_prompt"]
    old = (await client.get(ROOT, headers=owner)).json()[0]
    assert old["system_prompt"] == first["system_prompt"]
    assert old["published_expert_id"] == first["id"]
    duplicate = await client.post(
        f"{ROOT}/releases", headers=admin, json={"package": _package("2.0"), "reviewed": True}
    )
    assert duplicate.status_code == 409, duplicate.text


async def test_export_rejects_private_knowledge_and_install_uses_only_installers_choices(env):
    client, server, admin = env
    owner, peer = await _users(env)
    owner_id = (await client.get("/api/auth/me", headers=owner)).json()["id"]
    peer_id = (await client.get("/api/auth/me", headers=peer)).json()["id"]
    private_kb = server.services.knowledge_repo.create_base(
        owner_user_id=owner_id, name="Private source"
    )
    peer_kb = server.services.knowledge_repo.create_base(
        owner_user_id=peer_id, name="Installer source"
    )
    created = await client.post(
        ROOT, headers=owner, json=_config(knowledge_base_ids=[private_kb.id])
    )
    assert created.status_code == 201, created.text
    export_path = f"{ROOT}/{created.json()['agent_id']}/export"
    metadata = {"version": "1.0", "source": "Editorial team", "license": "Internal"}
    rejected = await client.post(export_path, headers=owner, json=metadata)
    assert rejected.status_code == 403, rejected.text
    updated = await client.put(
        f"{ROOT}/{created.json()['agent_id']}", headers=owner, json=_config()
    )
    assert updated.status_code == 200, updated.text
    exported = await client.post(export_path, headers=owner, json=metadata)
    assert exported.status_code == 200, exported.text
    for field in (
        "knowledge_base_ids",
        "mcp_servers",
        "default_model",
        "files",
        "credentials",
        "memory",
    ):
        assert field not in exported.json()
    published = await client.post(
        f"{ROOT}/releases", headers=admin, json={"package": exported.json(), "reviewed": True}
    )
    assert published.status_code == 201, published.text
    install_path = f"{ROOT}/releases/{published.json()['id']}/install"
    denied = await client.post(
        install_path, headers=peer, json=_config(knowledge_base_ids=[private_kb.id])
    )
    assert denied.status_code == 403, denied.text
    installed = await client.post(
        install_path, headers=peer, json=_config(knowledge_base_ids=[peer_kb.id])
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["knowledge_base_ids"] == [peer_kb.id]


@pytest.mark.parametrize(
    "field,value",
    [
        ("backend", {"type": "local_shell"}),
        ("mcp_servers", ["publisher-secret"]),
        ("quick_prompts", [{"prompt": "Private memory"}]),
        ("task_examples", {"zh": ["Private cron notes"]}),
        ("icon_url", "https://public.example/avatar.png"),
        ("files", {"USER.md": "Private user profile"}),
    ],
)
async def test_offline_package_rejects_legacy_workspace_and_runtime_fields(env, field, value):
    client, _server, admin = env
    response = await client.post(
        f"{ROOT}/releases",
        headers=admin,
        json={"package": _package(**{field: value}), "reviewed": True},
    )
    assert response.status_code == 422, response.text
    assert (await client.get(f"{ROOT}/releases", headers=admin)).json() == []


async def test_install_rechecks_model_grants_and_cannot_add_actions(env):
    client, server, admin = env
    owner, peer = await _users(env)
    edition = await _publish(client, admin)
    group = server.services.repos.user_group_repo.create(name="Model users", description=None)
    owner_id = (await client.get("/api/auth/me", headers=owner)).json()["id"]
    server.services.repos.user_group_member_repo.add(group.user_group_id, owner_id)
    GroupAccess(server.services.repos).set_visibility(
        RESOURCE_MODEL, "openai/gpt-4o", "groups", [group.user_group_id]
    )
    path = f"{ROOT}/releases/{edition['id']}/install"
    denied = await client.post(path, headers=peer, json=_config())
    assert denied.status_code == 403, denied.text
    denied = await client.post(path, headers=owner, json=_config(actions=["local_xlsx"]))
    assert denied.status_code == 403, denied.text
    installed = await client.post(path, headers=owner, json=_config())
    assert installed.status_code == 201, installed.text
    assert (await client.get(ROOT, headers=peer)).json() == []


async def test_new_edition_does_not_grant_added_actions(env):
    client, _server, admin = env
    owner, peer = await _users(env)
    first = await _publish(client, admin)
    second = await _publish(client, admin, version="2.0", actions=["local_docx", "local_xlsx"])
    denied = await client.post(
        f"{ROOT}/releases/{second['id']}/install",
        headers=peer,
        json=_config(actions=["local_docx", "local_xlsx"]),
    )
    assert denied.status_code == 403, denied.text
    for edition in (first, second):
        installed = await client.post(
            f"{ROOT}/releases/{edition['id']}/install",
            headers=owner,
            json=_config(name=f"Edition {edition['version']}"),
        )
        assert installed.status_code == 201, installed.text
        assert installed.json()["actions"] == ["local_docx"]


async def test_approved_skills_are_selectable_but_install_rechecks_group_grants(env):
    client, server, admin = env
    owner, peer = await _users(env)
    edition = await _publish(
        client,
        admin,
        skills=[
            {
                "name": "Fact checking",
                "content": "Preserve exact dates and amounts.",
                "source": "Editorial team",
                "license": "Internal",
            }
        ],
    )
    resources = (await client.get(f"{ROOT}/resources", headers=owner)).json()
    assert len(resources["skills"]) == 1
    skill_id = resources["skills"][0]["id"]
    group = server.services.repos.user_group_repo.create(
        name="Approved skill users", description=None
    )
    owner_id = (await client.get("/api/auth/me", headers=owner)).json()["id"]
    server.services.repos.user_group_member_repo.add(group.user_group_id, owner_id)
    GroupAccess(server.services.repos).set_visibility(
        RESOURCE_SKILL_PACKAGE, skill_id, "groups", [group.user_group_id]
    )
    path = f"{ROOT}/releases/{edition['id']}/install"
    denied = await client.post(path, headers=peer, json=_config())
    assert denied.status_code == 403, denied.text
    assert (await client.get(f"{ROOT}/resources", headers=peer)).json()["skills"] == []
    installed = await client.post(path, headers=owner, json=_config())
    assert installed.status_code == 201, installed.text
    assert installed.json()["skill_package_ids"] == [skill_id]
    server.services.repos.user_group_member_repo.remove(group.user_group_id, owner_id)
    changed = await client.put(
        f"{ROOT}/{installed.json()['agent_id']}",
        headers=owner,
        json=_config(skill_package_ids=[skill_id]),
    )
    assert changed.status_code == 403, changed.text
