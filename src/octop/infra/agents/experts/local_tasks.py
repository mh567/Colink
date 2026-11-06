"""Caller-authorized expert configuration for structured native office tasks."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from octop.infra.agents.providers.store import ProviderStore
from octop.infra.agents.settings.profile import id_list_from_row, parse_config_json
from octop.infra.db.repos.agents import AgentRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.groups import RESOURCE_SKILL_PACKAGE, GroupAccess, model_ref_allowed
from octop.infra.users.identity import User

REVIEWED_SKILL = "reviewed_local_skill"
Action = Literal["local_docx", "local_xlsx"]


class LocalExpertConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="", max_length=12000)
    default_model: str = Field(min_length=1, max_length=200)
    actions: list[Action] = Field(min_length=1, max_length=2)
    skill_package_ids: list[str] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=20)


def approved_skill(services: Any, package_id: str) -> dict[str, Any] | None:
    raw = services.control_file_repo.get(REVIEWED_SKILL, package_id, "review.json")
    return json.loads(raw) if raw else None


def assert_local_resources(services: Any, user: User, config: LocalExpertConfig) -> None:
    employee = services.user_repo.get(user.id)
    if (
        employee is None
        or employee.disabled
        or not set(config.actions).issubset(employee.permissions)
    ):
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "local actions not authorized")
    if not ProviderStore(provider_repo=services.provider_repo).is_model_ref_usable(
        config.default_model
    ):
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "model unavailable")
    if not model_ref_allowed(
        services.repos, user_id=user.id, is_admin=False, model_ref=config.default_model
    ):
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "model not authorized")
    visible = {base.id for base in services.knowledge_repo.list_visible(user.id)}
    if not set(config.knowledge_base_ids).issubset(visible):
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "knowledge not authorized")
    access = GroupAccess(services.repos)
    for package_id in config.skill_package_ids:
        row = services.skill_package_repo.get(package_id)
        if row is None or not row.shared or approved_skill(services, package_id) is None:
            raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "skill has not been reviewed")
        access.assert_allowed(
            user_id=user.id,
            is_admin=False,
            resource_type=RESOURCE_SKILL_PACKAGE,
            resource_key=package_id,
            is_owner=False,
        )


def expert_config(row: AgentRow) -> LocalExpertConfig:
    return LocalExpertConfig(
        name=row.name,
        description=row.description or "",
        system_prompt=row.system_prompt or "",
        default_model=row.default_model or "",
        actions=parse_config_json(row.config_json).get("local_actions", []),
        skill_package_ids=id_list_from_row(row, "skill_package_ids"),
        knowledge_base_ids=id_list_from_row(row, "knowledge_base_ids"),
    )


def owned_local_expert(services: Any, user: User, agent_id: str) -> AgentRow:
    row: AgentRow | None = services.agent_repo.get(agent_id)
    if (
        row is None
        or row.user_id != user.id
        or not row.enabled
        or not parse_config_json(row.config_json).get("local_only")
    ):
        raise OctopError(ErrorCode.FORBIDDEN, "local expert not owned by caller")
    return row


def validate_local_expert(services: Any, user: User, agent_id: str, format: str) -> AgentRow:
    row = owned_local_expert(services, user, agent_id)
    try:
        config = expert_config(row)
    except ValidationError as exc:
        raise OctopError(
            ErrorCode.RESOURCE_NOT_ALLOWED, "invalid local expert configuration"
        ) from exc
    if f"local_{format}" not in config.actions:
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "expert action not selected")
    assert_local_resources(services, user, config)
    return row


async def local_expert_context(
    services: Any,
    user: User,
    agent_id: str,
    format: str,
    instruction: str,
) -> tuple[str, str]:
    from octop.infra.knowledge.retrieve import retrieve_context

    row = validate_local_expert(services, user, agent_id, format)
    config = expert_config(row)
    parts = [config.system_prompt]
    for package_id in config.skill_package_ids:
        skill = approved_skill(services, package_id)
        if skill is None:
            raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "skill approval unavailable")
        parts.append(str(skill["content"]))
    if config.knowledge_base_ids:
        parts.append(
            await retrieve_context(
                services,
                user_id=user.id,
                is_admin=False,
                query=instruction,
                knowledge_base_ids=config.knowledge_base_ids,
                locale=user.locale,
            )
        )
    if expert_config(validate_local_expert(services, user, agent_id, format)) != config:
        raise OctopError(
            ErrorCode.RESOURCE_NOT_ALLOWED, "expert configuration changed during retrieval"
        )
    context = "\n\n".join(parts)
    if len(context) > 60000:
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, "selected expert context exceeds limit")
    return config.default_model, context


def local_resources(services: Any, user: User) -> dict[str, Any]:
    employee = services.user_repo.get(user.id)
    if employee is None or employee.disabled:
        raise OctopError(ErrorCode.FORBIDDEN, "employee unavailable")
    models = []
    providers = ProviderStore(provider_repo=services.provider_repo)
    for provider in services.provider_repo.list_all():
        if not provider.enabled:
            continue
        for model in provider.get_models():
            ref = f"{provider.name}/{model['id']}"
            if providers.is_model_ref_usable(ref) and model_ref_allowed(
                services.repos, user_id=user.id, is_admin=False, model_ref=ref
            ):
                models.append({"id": ref, "name": str(model.get("name") or model["id"])})
    access = GroupAccess(services.repos)
    skills = [
        {"id": row.id, "name": row.name}
        for row in services.skill_package_repo.list_visible(user.id)
        if row.shared
        and approved_skill(services, row.id) is not None
        and access.allows(
            user_id=user.id,
            is_admin=False,
            resource_type=RESOURCE_SKILL_PACKAGE,
            resource_key=row.id,
            is_owner=False,
        )
    ]
    return {
        "models": models,
        "actions": [
            {"id": action, "name": action}
            for action in ("local_docx", "local_xlsx")
            if action in employee.permissions
        ],
        "skills": skills,
        "knowledge_bases": [
            {"id": base.id, "name": base.name}
            for base in services.knowledge_repo.list_visible(user.id)
        ],
    }


def local_expert_payload(row: AgentRow) -> dict[str, Any]:
    return {
        **expert_config(row).model_dump(),
        "agent_id": row.agent_id,
        "published_expert_id": row.published_expert_id,
    }


async def save_local_expert(
    services: Any,
    registry: Any,
    user: User,
    config: LocalExpertConfig,
    *,
    agent_id: str | None = None,
    published_expert_id: str | None = None,
) -> dict[str, Any]:
    from octop.infra.agents.manager import AgentCreateSpec

    assert_local_resources(services, user, config)
    if agent_id is not None:
        row = owned_local_expert(services, user, agent_id)
        stored = parse_config_json(row.config_json)
        stored["local_actions"] = config.actions
        row = await registry.update(
            agent_id,
            name=config.name,
            description=config.description,
            system_prompt=config.system_prompt,
            default_model=config.default_model,
            config_json=json.dumps(stored),
            skill_package_ids=json.dumps(config.skill_package_ids),
            knowledge_base_ids=json.dumps(config.knowledge_base_ids),
        )
    else:
        row = await registry.create(
            AgentCreateSpec(
                name=config.name,
                user_id=user.id,
                description=config.description,
                system_prompt=config.system_prompt,
                default_model=config.default_model,
                config={"local_only": True, "local_actions": config.actions},
                skill_package_ids=config.skill_package_ids,
                knowledge_base_ids=config.knowledge_base_ids,
                published_expert_id=published_expert_id,
            ),
            defer_bootstrap=True,
        )
    return local_expert_payload(row)
