"""Reviewed, immutable offline expert editions using existing snapshot storage."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from octop.infra.agents.experts.local_tasks import (
    REVIEWED_SKILL,
    LocalActions,
    LocalExpertConfig,
    approved_skill,
    assert_local_resources,
    expert_config,
    owned_local_expert,
    save_local_expert,
)
from octop.infra.agents.experts.snapshot_store import read_snapshot_manifest
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.utils.ulid import new_ulid


class OfflineSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=12000)
    source: str = Field(min_length=1, max_length=2000)
    license: str = Field(min_length=1, max_length=4000)


class OfflinePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source: str = Field(min_length=1, max_length=2000)
    license: str = Field(min_length=1, max_length=4000)
    system_prompt: str = Field(default="", max_length=12000)
    actions: LocalActions
    skills: list[OfflineSkill] = Field(default_factory=list, max_length=20)

    @field_validator("skills")
    @classmethod
    def unique_skill_names(cls, skills: list[OfflineSkill]) -> list[OfflineSkill]:
        if len({skill.name for skill in skills}) != len(skills):
            raise ValueError("skill names must be unique within an edition")
        return skills


def require_admin(user: User) -> None:
    if not user.is_admin:
        raise OctopError(ErrorCode.FORBIDDEN, "administrator review required")


def release_manifest(services: Any, release_id: str) -> dict[str, Any]:
    row = services.published_expert_repo.get(release_id)
    manifest = read_snapshot_manifest(services.control_file_repo, release_id)
    if row is None or not manifest.get("local_release"):
        raise OctopError(ErrorCode.NOT_FOUND, "reviewed local edition not found")
    return manifest


def releases(services: Any) -> list[dict[str, Any]]:
    result = []
    for row in services.published_expert_repo.list_all():
        manifest = read_snapshot_manifest(services.control_file_repo, row.id)
        if manifest.get("local_release"):
            result.append({"id": row.id, **manifest["package"]})
    return result


def publish_offline(
    services: Any, user: User, package: OfflinePackage, *, reviewed: bool
) -> dict[str, Any]:
    require_admin(user)
    if not reviewed:
        raise OctopError(
            ErrorCode.FORBIDDEN, "review sources, licenses, content and permissions first"
        )
    slug = f"{package.name}@{package.version}"
    if services.published_expert_repo.get_by_slug(slug) is not None:
        raise OctopError.localized(ErrorCode.PUBLISHED_EXPERT_SLUG_TAKEN, slug=slug)
    release_id = new_ulid()
    skills = [
        (
            new_ulid(),
            f"{package.name} {package.version} · {skill.name}",
            skill.source,
            skill.model_dump_json().encode(),
        )
        for skill in package.skills
    ]
    manifest = {
        "id": slug,
        "label": {"zh": package.name, "en": package.name},
        "local_release": True,
        "package": package.model_dump(),
        "skill_package_ids": [item[0] for item in skills],
        "reviewed_by": user.id,
    }
    try:
        services.published_expert_repo.create_offline_edition(
            id=release_id,
            slug=slug,
            name=package.name,
            description=package.description,
            created_by=str(user.id),
            actor=user.username,
            manifest=json.dumps(manifest, ensure_ascii=False).encode(),
            skill_namespace=REVIEWED_SKILL,
            skills=skills,
        )
    except Exception:
        if services.published_expert_repo.get_by_slug(slug) is not None:
            raise OctopError.localized(ErrorCode.PUBLISHED_EXPERT_SLUG_TAKEN, slug=slug) from None
        raise
    return {"id": release_id, **package.model_dump()}


def export_offline(
    services: Any, user: User, agent_id: str, *, version: str, source: str, license: str
) -> OfflinePackage:
    row = owned_local_expert(services, user, agent_id)
    config = expert_config(row)
    assert_local_resources(services, user, config)
    if config.knowledge_base_ids:
        raise OctopError(
            ErrorCode.LOCAL_EXPERT_PRIVATE_REFERENCE,
            "remove knowledge references before exporting; no content is copied",
        )
    skills = []
    for package_id in config.skill_package_ids:
        record = services.skill_package_repo.get(package_id)
        skill = approved_skill(services, package_id)
        if record is None or not record.shared or skill is None:
            raise OctopError(
                ErrorCode.RESOURCE_NOT_ALLOWED, "private or unreviewed skill cannot be exported"
            )
        skills.append(OfflineSkill.model_validate(skill))
    if len({skill.name for skill in skills}) != len(skills):
        raise OctopError.localized(ErrorCode.LOCAL_EXPERT_DUPLICATE_SKILL)
    return OfflinePackage(
        name=config.name,
        description=config.description,
        version=version,
        source=source,
        license=license,
        system_prompt=config.system_prompt,
        actions=config.actions,
        skills=skills,
    )


async def install_offline(
    services: Any, registry: Any, user: User, release_id: str, config: LocalExpertConfig
) -> dict[str, Any]:
    manifest = release_manifest(services, release_id)
    package = OfflinePackage.model_validate(manifest["package"])
    if not set(config.actions).issubset(package.actions):
        raise OctopError(
            ErrorCode.RESOURCE_NOT_ALLOWED, "edition does not provide selected actions"
        )
    try:
        installed = LocalExpertConfig.model_validate(
            {
                **config.model_dump(),
                "system_prompt": package.system_prompt,
                "skill_package_ids": list(
                    dict.fromkeys([*manifest["skill_package_ids"], *config.skill_package_ids])
                ),
            }
        )
    except ValidationError as exc:
        raise OctopError(
            ErrorCode.RESOURCE_NOT_ALLOWED, "too many selected edition skills"
        ) from exc
    return await save_local_expert(
        services, registry, user, installed, published_expert_id=release_id
    )
