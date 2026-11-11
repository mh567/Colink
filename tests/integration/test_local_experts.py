"""Local experts use caller-owned resources and reviewed offline releases."""

from typing import Any

from tests.support.auth import create_user


async def test_offline_release_requires_admin_review_and_preserves_versions(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, _server, admin = env
    employee = await create_user(client, admin, username="release_employee")
    package = {
        "name": "Report helper",
        "description": "Prepare a report",
        "version": "1.0",
        "source": "Enterprise editorial team",
        "license": "Company-owned; internal redistribution permitted",
        "system_prompt": "Keep the supplied facts unchanged.",
        "actions": ["local_docx"],
        "skills": [],
    }
    payload = {"package": package, "reviewed": True}
    denied = await client.post("/api/experts/local/releases", headers=employee, json=payload)
    assert denied.status_code == 403, denied.text
    unreviewed = await client.post(
        "/api/experts/local/releases", headers=admin, json={**payload, "reviewed": False}
    )
    assert unreviewed.status_code == 403, unreviewed.text
    first = await client.post("/api/experts/local/releases", headers=admin, json=payload)
    assert first.status_code == 201, first.text
    package["version"] = "2.0"
    package["system_prompt"] = "Keep facts and cite sources."
    second = await client.post("/api/experts/local/releases", headers=admin, json=payload)
    assert second.status_code == 201, second.text
    listed = await client.get("/api/experts/local/releases", headers=employee)
    assert {r["version"] for r in listed.json()} == {"1.0", "2.0"}
    assert first.json()["id"] != second.json()["id"]
    duplicate = await client.post("/api/experts/local/releases", headers=admin, json=payload)
    assert duplicate.status_code == 409, duplicate.text


async def test_local_expert_can_use_all_office_actions_including_offline_roundtrip(env):
    from tests.support.auth import seed_openai_provider

    client, _server, admin = env
    await seed_openai_provider(client, admin)
    actions = ["local_docx", "local_xlsx", "local_pptx"]
    employee = await create_user(
        client, admin, username="office_employee", permissions=[*actions, "experts"]
    )
    root = "/api/experts/local"
    config = {"name": "Office reports", "default_model": "openai/gpt-4o", "actions": actions}
    created = await client.post(root, headers=employee, json=config)
    assert created.status_code == 201, created.text
    assert created.json()["actions"] == actions
    agent_id = created.json()["agent_id"]
    updated = await client.put(
        f"{root}/{agent_id}", headers=employee, json={**config, "description": "Office reports"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["actions"] == actions
    exported = await client.post(
        f"{root}/{agent_id}/export",
        headers=employee,
        json={"version": "1", "source": "company", "license": "internal"},
    )
    assert exported.status_code == 200, exported.text
    package = exported.json()
    assert package["actions"] == actions
    published = await client.post(
        f"{root}/releases", headers=admin, json={"reviewed": True, "package": package}
    )
    assert published.status_code == 201, published.text
    release_id = published.json()["id"]
    installed = await client.post(
        f"{root}/releases/{release_id}/install",
        headers=employee,
        json={**config, "name": "Installed office reports"},
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["actions"] == actions


async def test_offline_package_cannot_carry_private_references_or_executable_files(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, _server, admin = env
    base = {
        "name": "Unsafe package",
        "version": "1",
        "source": "test",
        "license": "MIT",
        "system_prompt": "Draft a report",
        "actions": ["local_docx"],
        "skills": [],
    }
    for extra in (
        {"knowledge_base_ids": ["private"]},
        {"files": {"run.py": "pass"}},
        {"credentials": "secret"},
    ):
        response = await client.post(
            "/api/experts/local/releases/preview", headers=admin, json={**base, **extra}
        )
        assert response.status_code == 422, response.text


async def test_private_local_configuration_and_export_never_open_to_admin_or_peer(env):
    from tests.support.auth import seed_openai_provider

    client, server, admin = env
    await seed_openai_provider(client, admin)
    owner = await create_user(
        client, admin, username="local_owner", permissions=["local_docx", "experts"]
    )
    peer = await create_user(
        client, admin, username="local_peer", permissions=["local_docx", "experts"]
    )
    body = {
        "name": "Private writer",
        "default_model": "openai/gpt-4o",
        "system_prompt": "Private editorial notes",
        "actions": ["local_docx"],
        "skill_package_ids": [],
        "knowledge_base_ids": [],
    }
    created = await client.post("/api/experts/local", headers=owner, json=body)
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]
    for other in (admin, peer):
        assert (await client.get("/api/experts/local", headers=other)).json() == []
        assert (
            await client.put(f"/api/experts/local/{agent_id}", headers=other, json=body)
        ).status_code == 403
        assert (await client.get(f"/api/agents/{agent_id}", headers=other)).status_code == 403
        response = await client.post(
            f"/api/experts/local/{agent_id}/export",
            headers=other,
            json={"version": "1", "source": "company", "license": "internal"},
        )
        assert response.status_code == 403
    exported = await client.post(
        f"/api/experts/local/{agent_id}/export",
        headers=owner,
        json={"version": "1", "source": "company", "license": "internal"},
    )
    assert exported.status_code == 200, exported.text
    assert "knowledge_base_ids" not in exported.json()
    denied = await client.post(
        f"/api/agents/{agent_id}/publish-expert", headers=owner, json={"name": "Bypass"}
    )
    assert denied.status_code == 403, denied.text
    denied = await client.patch(f"/api/agents/{agent_id}", headers=owner, json={"is_shared": True})
    assert denied.status_code == 403, denied.text
    row = server.services.agent_repo.get(agent_id)
    base = server.services.knowledge_repo.create_base(
        owner_user_id=row.user_id, name="Private data"
    )
    body["knowledge_base_ids"] = [base.id]
    assert (
        await client.put(f"/api/experts/local/{agent_id}", headers=owner, json=body)
    ).status_code == 200
    rejected = await client.post(
        f"/api/experts/local/{agent_id}/export",
        headers=owner,
        json={"version": "2", "source": "company", "license": "internal"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "LOCAL_EXPERT_PRIVATE_REFERENCE"


async def test_approved_skills_and_installed_editions_use_installers_resources(env):
    from tests.support.auth import seed_openai_provider

    client, server, admin = env
    await seed_openai_provider(client, admin)
    owner = await create_user(
        client,
        admin,
        username="edition_user",
        permissions=["local_docx", "experts", "skill_packages"],
    )
    other = await create_user(
        client, admin, username="edition_other", permissions=["local_docx", "experts"]
    )
    package = {
        "name": "Reviewed writer",
        "version": "1",
        "source": "Internal team",
        "license": "MIT",
        "system_prompt": "Keep facts",
        "actions": ["local_docx"],
        "skills": [
            {
                "name": "Summary",
                "content": "Always include a short summary.",
                "source": "Internal team",
                "license": "MIT",
            }
        ],
    }
    response = await client.post(
        "/api/experts/local/releases", headers=admin, json={"package": package, "reviewed": True}
    )
    assert response.status_code == 201, response.text
    release = response.json()
    choices = (await client.get("/api/experts/local/resources", headers=owner)).json()
    assert len(choices["skills"]) == 1
    config = {
        "name": "My installed writer",
        "default_model": "openai/gpt-4o",
        "actions": ["local_docx"],
    }
    installed = await client.post(
        f"/api/experts/local/releases/{release['id']}/install", headers=owner, json=config
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["system_prompt"] == "Keep facts"
    assert installed.json()["skill_package_ids"] == [choices["skills"][0]["id"]]
    assert installed.json()["knowledge_base_ids"] == []
    assert (
        await client.get(f"/api/agents/{installed.json()['agent_id']}", headers=other)
    ).status_code == 403
    forged = await client.post(
        f"/api/experts/local/releases/{release['id']}/install",
        headers=owner,
        json={**config, "name": "Forged", "actions": ["local_xlsx"]},
    )
    assert forged.status_code == 403
    ordinary_share = await client.post(
        "/api/skill-packages", headers=owner, json={"name": "Unreviewed", "shared": True}
    )
    assert ordinary_share.status_code == 403
    package_id = choices["skills"][0]["id"]
    mutated = await client.patch(
        f"/api/skill-packages/{package_id}", headers=admin, json={"name": "Changed without review"}
    )
    assert mutated.status_code == 403
    user = server.user_manager.get_by_id(
        server.services.agent_repo.get(installed.json()["agent_id"]).user_id
    )
    from octop.infra.agents.experts.local_tasks import local_expert_context

    model, context = await local_expert_context(
        server.services, user, installed.json()["agent_id"], "docx", "Summarize"
    )
    assert model == "openai/gpt-4o"
    assert "Always include a short summary." in context


async def test_chinese_release_names_do_not_collide_and_duplicate_skills_are_rejected(env):
    client, _server, admin = env
    package = {
        "name": "报告助手",
        "version": "1.0",
        "source": "企业自有",
        "license": "内部使用",
        "actions": ["local_docx"],
        "skills": [],
    }
    for name in ("报告助手", "财务助手"):
        response = await client.post(
            "/api/experts/local/releases",
            headers=admin,
            json={"package": {**package, "name": name}, "reviewed": True},
        )
        assert response.status_code == 201, response.text
    skill = {"name": "摘要", "content": "保留事实", "source": "企业自有", "license": "内部使用"}
    response = await client.post(
        "/api/experts/local/releases/preview",
        headers=admin,
        json={**package, "skills": [skill, skill]},
    )
    assert response.status_code == 422


async def test_offline_edition_rolls_back_all_records_after_skill_conflict(env):
    from sqlite3 import IntegrityError

    import pytest

    from octop.infra.agents.experts.local_tasks import REVIEWED_SKILL

    _client, server, _admin = env
    services = server.services
    release_id = "rollback-edition"
    skill_id = "rollback-skill"
    with pytest.raises(IntegrityError):
        services.published_expert_repo.create_offline_edition(
            id=release_id,
            slug="rollback@1",
            name="Rollback edition",
            description="",
            created_by="1",
            actor="admin",
            manifest=b"{}",
            skill_namespace=REVIEWED_SKILL,
            skills=[
                (skill_id, "First skill", "Internal team", b"{}"),
                (skill_id, "Conflicting skill", "Internal team", b"{}"),
            ],
        )
    assert services.published_expert_repo.get(release_id) is None
    assert services.published_expert_repo.get_by_slug("rollback@1") is None
    assert services.skill_package_repo.get(skill_id) is None
    assert not services.control_file_repo.has_owner("published_expert", release_id)
    assert not services.control_file_repo.has_owner(REVIEWED_SKILL, skill_id)


async def test_install_rejects_more_than_twenty_merged_skills_without_creating_agent(env):
    from tests.support.auth import seed_openai_provider

    client, _server, admin = env
    await seed_openai_provider(client, admin)
    owner = await create_user(
        client, admin, username="skill_limit_owner", permissions=["local_docx", "experts"]
    )
    package = {
        "name": "Full edition",
        "version": "1",
        "source": "Internal team",
        "license": "MIT",
        "actions": ["local_docx"],
        "skills": [
            {"name": f"Skill {i}", "content": "Keep facts", "source": "Team", "license": "MIT"}
            for i in range(20)
        ],
    }
    release = await client.post(
        "/api/experts/local/releases", headers=admin, json={"package": package, "reviewed": True}
    )
    assert release.status_code == 201, release.text
    extra = await client.post(
        "/api/experts/local/releases",
        headers=admin,
        json={
            "package": {**package, "name": "Extra edition", "skills": package["skills"][:1]},
            "reviewed": True,
        },
    )
    assert extra.status_code == 201, extra.text
    resources = await client.get("/api/experts/local/resources", headers=owner)
    assert resources.status_code == 200, resources.text
    extra_skill = next(
        skill["id"] for skill in resources.json()["skills"] if "Extra edition" in skill["name"]
    )
    config = {
        "name": "My writer",
        "default_model": "openai/gpt-4o",
        "actions": ["local_docx"],
    }
    endpoint = f"/api/experts/local/releases/{release.json()['id']}/install"
    denied = await client.post(
        endpoint, headers=owner, json={**config, "skill_package_ids": [extra_skill]}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "RESOURCE_NOT_ALLOWED"
    agents = await client.get("/api/agents", headers=owner)
    assert agents.status_code == 200, agents.text
    assert agents.json() == []
    installed = await client.post(endpoint, headers=owner, json=config)
    assert installed.status_code == 201, installed.text
    assert len(installed.json()["skill_package_ids"]) == 20


async def test_local_expert_api_docs_have_typed_schemas(tmp_octop_home):
    from tests.support.app import octop_client, write_octop_config

    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home) as (client, _server):
        docs = await client.get("/api/docs")
        assert docs.status_code == 200
        assert "/api/openapi.json" in docs.text
        schema = (await client.get("/api/openapi.json")).json()
    operations = [
        operation
        for path, methods in schema["paths"].items()
        if path.startswith("/api/experts/local")
        for operation in methods.values()
    ]
    assert len(operations) == 9
    for operation in operations:
        assert operation["summary"]
        assert operation["tags"] == ["experts"]
        status = "201" if "201" in operation["responses"] else "200"
        assert operation["responses"][status]["content"]["application/json"]["schema"]


async def test_export_rejects_same_named_skills_from_different_approved_versions(env):
    from tests.support.auth import seed_openai_provider

    client, _server, admin = env
    await seed_openai_provider(client, admin)
    owner = await create_user(
        client, admin, username="duplicate_skill_owner", permissions=["local_docx", "experts"]
    )
    package = {
        "name": "Versioned writer",
        "source": "Internal team",
        "license": "MIT",
        "actions": ["local_docx"],
        "skills": [
            {"name": "Summary", "content": "Keep facts", "source": "Team", "license": "MIT"}
        ],
    }
    for version in ("1", "2"):
        release = await client.post(
            "/api/experts/local/releases",
            headers=admin,
            json={"package": {**package, "version": version}, "reviewed": True},
        )
        assert release.status_code == 201, release.text
    resources = await client.get("/api/experts/local/resources", headers=owner)
    assert resources.status_code == 200, resources.text
    skills = resources.json()["skills"]
    assert len(skills) == 2
    skill_ids = [skill["id"] for skill in skills]
    created = await client.post(
        "/api/experts/local",
        headers=owner,
        json={
            "name": "Combined writer",
            "default_model": "openai/gpt-4o",
            "actions": ["local_docx"],
            "skill_package_ids": skill_ids,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["skill_package_ids"] == skill_ids
    agent_id = created.json()["agent_id"]
    exported = await client.post(
        f"/api/experts/local/{agent_id}/export",
        headers=owner,
        json={"version": "1", "source": "Internal team", "license": "MIT"},
    )
    assert exported.status_code == 409, exported.text
    assert exported.json()["error"]["code"] == "LOCAL_EXPERT_DUPLICATE_SKILL"
    personal = await client.get("/api/experts/local", headers=owner)
    assert personal.status_code == 200, personal.text
    assert personal.json()[0]["skill_package_ids"] == skill_ids
