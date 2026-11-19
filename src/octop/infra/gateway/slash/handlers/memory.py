"""Explicit memory maintenance commands for authenticated local chat users."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop_harness.slash import SlashCommand, SlashSink

from octop.i18n import tr as full_tr
from octop.i18n.domains.slash import tr
from octop.infra.gateway.slash.ctx import SlashCtx, lang_of
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.utils.locale import Locale

if TYPE_CHECKING:
    from octop.infra.gateway.slash.dispatcher import SlashDispatcher


def _status_line(state: dict[str, Any], locale: str) -> str:
    phase = state["phase"]
    if phase in {"queued", "skipped"}:
        label = full_tr(f"slash.memory.{phase}", locale)
    else:
        label = full_tr(f"memory_slim.{phase}", locale)
    line = f"[{state['agent_id']}] {label}"
    started = state.get("started_at")
    if started:
        end = state.get("updated_at", started) if phase in {"done", "failed"} else time.time()
        line += " " + full_tr("memory_slim.elapsed", locale, seconds=max(0, int(end - started)))
    if phase == "deduplicating" and "scanned" in state and "total" in state:
        line += " " + full_tr(
            "memory_slim.scanned", locale, scanned=state["scanned"], total=state["total"]
        )
    report = state.get("report")
    sweep = state.get("sweep")
    if phase == "done" and isinstance(sweep, dict):
        line += "\n" + full_tr(
            "memory_slim.swept",
            locale,
            namespaces=sweep.get("namespaces", 0),
            threads=sweep.get("threads", 0),
            directories=sweep.get("directories", 0),
        )
    if phase == "done" and report and report.get("backend") == "postgres":
        line += "\n" + full_tr(
            "memory_slim.postgres_result",
            locale,
            candidates=report["candidates_deleted"],
            journal=report["journal_deleted"],
        )
    elif phase == "done" and report and report.get("operation") == "restore":
        line += "\n" + full_tr(
            "memory_slim.restore_result", locale, backup=Path(report["backup_path"]).name
        )
    elif phase == "done" and report:
        line += "\n" + full_tr(
            "memory_slim.result",
            locale,
            before=f"{report['before']['file_bytes'] / 1048576:.2f}",
            after=f"{report['after']['file_bytes'] / 1048576:.2f}",
            backup=Path(report["backup_path"]).name,
        )
    if phase == "failed":
        line += " " + full_tr("slash.memory.failure_hint", locale)
    return line


def _format_bytes(size: int) -> str:
    if abs(size) < 1048576:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1048576:.2f} MB"


def _compact_result(stats: Any, locale: Locale) -> str:
    before = getattr(stats, "bytes_before", None)
    after = getattr(stats, "bytes_after", None)
    if before is None or after is None:
        line = tr("memory.compact_done_tables", locale, count=getattr(stats, "tables_done", 0))
    else:
        saved = before - after
        key = "memory.compact_done_saved" if saved > 0 else "memory.compact_done_unchanged"
        line = tr(
            key,
            locale,
            before=_format_bytes(before),
            after=_format_bytes(after),
            saved=_format_bytes(saved),
        )
    skipped = getattr(stats, "tables_skipped", ())
    if skipped:
        line += "\n" + tr("memory.compact_skipped", locale, tables=", ".join(skipped))
    return line


class _MemoryServerAdapter:
    def __init__(self, agent_manager: Any) -> None:
        self.app_runtime = type("AppRuntime", (), {"agent_registry": agent_manager})()
        repos = getattr(agent_manager, "_repos", None)
        config = getattr(agent_manager, "_config", None)
        agent_repo = getattr(repos, "agent_repo", None) if repos is not None else None
        self.services = type(
            "Services",
            (),
            {
                "agent_repo": agent_repo,
                "config": config,
                "repos": repos,
            },
        )()


async def cmd_memory(d: SlashDispatcher, cmd: SlashCommand, ctx: SlashCtx, sink: SlashSink) -> None:
    lang = lang_of(ctx)
    # External IM user_id is the agent owner's persistence identity, NOT proof
    # that the sender is the owner. Do not authorize maintenance with that id.
    if not ThreadRegistry.is_virtual_channel(ctx.channel_type):
        await sink.text(tr("memory.trusted_chat", lang))
        return
    user = ctx.user_repo.get(ctx.user_id) if ctx.user_repo is not None else None
    if user is None or user.disabled or ctx.user_id <= 0:
        await sink.text(full_tr("memory_slim.forbidden", lang))
        return

    class _UserProxy:
        def __init__(self, u: Any, uid: int) -> None:
            self._u = u
            self.id = getattr(u, "id", None) or uid

        def __getattr__(self, name: str) -> Any:
            return getattr(self._u, name)

    user_proxy = _UserProxy(user, ctx.user_id)

    args = cmd.args.split()
    if not args:
        await sink.text(tr("memory.usage", lang))
        return

    subcmd = args[0].lower()
    if subcmd == "explain":
        await sink.text(tr("memory.explain", lang))
        return

    if subcmd in {"save", "search", "query", "list", "forget"}:
        adapter = _MemoryServerAdapter(ctx.agent_manager)
        if subcmd == "save":
            raw_content = cmd.args.strip()[4:].strip()
            if not raw_content:
                await sink.text(tr("memory.save_usage", lang))
                return
            topic: str | None = None
            assertion = raw_content
            if raw_content.startswith("[") and "]" in raw_content:
                idx = raw_content.index("]")
                topic = raw_content[1:idx].strip()
                assertion = raw_content[idx + 1 :].strip()
            else:
                parts = raw_content.split(None, 1)
                if (
                    len(parts) == 2
                    and len(parts[0]) <= 20
                    and not any(p in parts[0] for p in "，。！？,.!?")
                ):
                    topic = parts[0]
                    assertion = parts[1]
            if not assertion:
                await sink.text(tr("memory.save_usage", lang))
                return
            from octop.api.common.memory_client import call_memory_rpc
            from octop.infra.errors import ErrorCode, OctopError

            try:
                res = call_memory_rpc(
                    agent_id=ctx.agent_id,
                    method="create_atom",
                    params={
                        "assertion": assertion,
                        "entity_name": topic,
                        "kind": "Preference",
                    },
                    user=user_proxy,
                    as_user=None,
                    server=adapter,
                )
                atom_id = res.get("id") or res.get("atom_id") or "saved"
                await sink.text(tr("memory.saved", lang, atom_id=atom_id, assertion=assertion))
            except OctopError as exc:
                if exc.code == ErrorCode.FORBIDDEN:
                    await sink.text(tr("memory.forbidden", lang))
                else:
                    await sink.text(exc.message)
            return

        if subcmd in {"search", "query"}:
            query = cmd.args.strip()[len(subcmd) :].strip()
            if not query:
                await sink.text(tr("memory.search_usage", lang))
                return
            from octop.api.common.memory_client import call_memory_rpc
            from octop.infra.errors import ErrorCode, OctopError

            try:
                res = call_memory_rpc(
                    agent_id=ctx.agent_id,
                    method="list_atoms",
                    params={
                        "query": query,
                        "status": "active",
                        "limit": 10,
                    },
                    user=user_proxy,
                    as_user=None,
                    server=adapter,
                )
                items = res.get("items") or []
                if not items:
                    await sink.text(tr("memory.not_found", lang))
                else:
                    lines = []
                    for item in items:
                        name_suffix = (
                            f" (主题: {item['entity_name']})" if item.get("entity_name") else ""
                        )
                        lines.append(
                            f"- [{item.get('id', '')}] {item.get('assertion', '')}{name_suffix}"
                        )
                    await sink.text("\n".join(lines))
            except OctopError as exc:
                if exc.code == ErrorCode.FORBIDDEN:
                    await sink.text(tr("memory.forbidden", lang))
                else:
                    await sink.text(exc.message)
            return

        if subcmd == "list":
            from octop.api.common.memory_client import call_memory_rpc
            from octop.infra.errors import ErrorCode, OctopError

            try:
                res = call_memory_rpc(
                    agent_id=ctx.agent_id,
                    method="list_atoms",
                    params={
                        "status": "active",
                        "limit": 20,
                    },
                    user=user_proxy,
                    as_user=None,
                    server=adapter,
                )
                items = res.get("items") or []
                if not items:
                    await sink.text(tr("memory.list_empty", lang))
                else:
                    lines = []
                    for item in items:
                        name_suffix = (
                            f" (主题: {item['entity_name']})" if item.get("entity_name") else ""
                        )
                        lines.append(
                            f"- [{item.get('id', '')}] {item.get('assertion', '')}{name_suffix}"
                        )
                    await sink.text(
                        tr("memory.list_header", lang, count=len(items)) + "\n" + "\n".join(lines)
                    )
            except OctopError as exc:
                if exc.code == ErrorCode.FORBIDDEN:
                    await sink.text(tr("memory.forbidden", lang))
                else:
                    await sink.text(exc.message)
            return

        if subcmd == "forget":
            forget_target = cmd.args.strip()[6:].strip()
            if not forget_target:
                await sink.text(tr("memory.forget_usage", lang))
                return
            from octop.api.common.memory_client import call_memory_rpc
            from octop.infra.agents.memory.forget import forget_memory_atom_for_agent
            from octop.infra.errors import ErrorCode, OctopError

            try:
                try:
                    res = await forget_memory_atom_for_agent(
                        adapter, ctx.agent_id, forget_target, user=user_proxy
                    )
                    assertion = res.get("assertion") or ""
                    await sink.text(
                        tr("memory.forgotten", lang, atom_id=forget_target, assertion=assertion)
                    )
                    return
                except OctopError as exc:
                    if exc.code != ErrorCode.NOT_FOUND:
                        raise

                search_res = call_memory_rpc(
                    agent_id=ctx.agent_id,
                    method="list_atoms",
                    params={
                        "query": forget_target,
                        "status": "active",
                        "limit": 5,
                    },
                    user=user_proxy,
                    as_user=None,
                    server=adapter,
                )
                items = search_res.get("items") or []
                if not items:
                    await sink.text(tr("memory.not_found", lang))
                elif len(items) == 1:
                    atom_id = items[0]["id"]
                    res = await forget_memory_atom_for_agent(
                        adapter, ctx.agent_id, atom_id, user=user_proxy
                    )
                    assertion = res.get("assertion") or items[0].get("assertion", "")
                    await sink.text(
                        tr("memory.forgotten", lang, atom_id=atom_id, assertion=assertion)
                    )
                else:
                    lines = [
                        f"- [{item.get('id', '')}] {item.get('assertion', '')}" for item in items
                    ]
                    await sink.text(tr("memory.multiple_found", lang, items="\n".join(lines)))
            except OctopError as exc:
                if exc.code == ErrorCode.FORBIDDEN:
                    await sink.text(tr("memory.forbidden", lang))
                else:
                    await sink.text(exc.message)
            return

    is_slim = subcmd == "slim"
    is_compact = subcmd == "compact"
    flags = [x.lower() for x in args[1:]]
    valid_slim = (
        is_slim
        and len(flags) == len(set(flags))
        and set(flags) <= {"--all", "--confirm", "--delete-dirs"}
    )
    valid_compact = is_compact and len(flags) == len(set(flags)) and set(flags) <= {"--confirm"}
    if not valid_slim and not valid_compact and args != ["status"]:
        await sink.text(tr("memory.usage", lang))
        return
    if ctx.agent_manager is None:
        await sink.text(full_tr("memory_slim.not_ready", lang))
        return
    coordinator = ctx.agent_manager.memory_slim
    try:
        if args[0] == "slim":
            all_agents = "--all" in flags
            if "--confirm" not in flags:
                targets = coordinator.preview_chat(
                    ctx.agent_id, ctx.user_id, all_agents=all_agents, locale=lang
                )
                command = "/memory slim" + (" --all" if all_agents else "") + " --confirm"
                await sink.text(tr("memory.preview", lang, count=len(targets)))
                for target in targets:
                    await sink.text(f"- {target['name']} [{target['agent_id']}]")
                directories = coordinator.orphan_directories()
                if directories:
                    delete_command = command + " --delete-dirs"
                    await sink.text(tr("memory.orphan_dirs", lang, command=delete_command))
                    for directory in directories:
                        await sink.text(f"- {directory}")
                else:
                    await sink.text(tr("memory.orphan_dirs_none", lang))
                await sink.text(tr("memory.confirm_hint", lang, command=command))
                return
            count = coordinator.start_chat(
                ctx.agent_id,
                ctx.user_id,
                all_agents=all_agents,
                locale=lang,
                remove_directories="--delete-dirs" in flags,
            )
            await sink.text(tr("memory.accepted", lang, count=count))
            return
        if args[0] == "compact":
            if "--confirm" not in flags:
                await sink.text(tr("memory.compact_preview", lang))
                await sink.text(
                    tr("memory.confirm_hint", lang, command="/memory compact --confirm")
                )
                return
            import asyncio

            from octop.infra.agents.memory.compact_store import compact_agent_store

            stats = await asyncio.to_thread(
                compact_agent_store, ctx.agent_manager, ctx.agent_id, locale=lang
            )
            await sink.text(_compact_result(stats, lang))
            return
        snapshot = coordinator.chat_status(ctx.agent_id, ctx.user_id, locale=lang)
        states = snapshot["agents"]
        if not states:
            await sink.text(tr("memory.idle", lang))
            return
        await sink.text(
            tr(
                "memory.summary",
                lang,
                completed=sum(s["phase"] == "done" for s in states),
                total=len(states),
            )
        )
        for state in states:
            await sink.text(_status_line(state, lang))
    except ValueError as exc:
        await sink.text(str(exc))
