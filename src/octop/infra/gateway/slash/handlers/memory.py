"""Explicit memory maintenance commands for authenticated local chat users."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop_harness.slash import SlashCommand, SlashSink

from octop.i18n import tr as full_tr
from octop.i18n.domains.slash import tr
from octop.infra.agents.memory.client import call_memory_rpc
from octop.infra.agents.memory.personal import forget_memory_atom_for_agent
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.slash.ctx import SlashCtx, lang_of
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.users.identity import User
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


async def _personal_command(
    subcmd: str, value: str, ctx: SlashCtx, user: User, sink: SlashSink
) -> None:
    lang = lang_of(ctx)
    registry = ctx.agent_manager
    if registry is None:
        await sink.text(full_tr("memory_slim.not_ready", lang))
        return

    async def rpc(method: str, params: dict[str, Any]) -> Any:
        return await asyncio.to_thread(
            call_memory_rpc,
            registry=registry,
            agent_id=ctx.agent_id,
            method=method,
            params=params,
            user=user,
        )

    try:
        if subcmd == "save":
            topic = None
            if value.startswith("["):
                label, closing, value = value[1:].partition("]")
                if not closing or not label.strip():
                    value = ""
                topic = label.strip()
            assertion = value.strip()
            if not assertion:
                await sink.text(tr("memory.save_usage", lang))
                return
            result = await rpc(
                "create_atom",
                {
                    "assertion": assertion,
                    "entity_name": topic or "User",
                    "entity_type": "User" if topic is None else "Project",
                    "kind": "Preference",
                },
            )
            await sink.text(
                tr("memory.saved", lang, atom_id=result["atom"]["id"], assertion=assertion)
            )
            return
        if subcmd == "forget":
            if not value:
                await sink.text(tr("memory.forget_usage", lang))
                return
            try:
                await forget_memory_atom_for_agent(registry, ctx.agent_id, value, user=user)
            except OctopError as exc:
                if exc.code != ErrorCode.NOT_FOUND:
                    raise
                result = await rpc("list_atoms", {"query": value, "limit": 5})
                items = result["items"]
                if items:
                    matches = "\n".join(f"- [{a['id']}] {a['assertion']}" for a in items)
                    await sink.text(tr("memory.forget_choose_id", lang, items=matches))
                else:
                    await sink.text(tr("memory.not_found", lang))
                return
            await sink.text(tr("memory.forgotten", lang, atom_id=value))
            return
        if subcmd in {"search", "query"} and not value:
            await sink.text(tr("memory.search_usage", lang))
            return
        result = await rpc(
            "list_atoms", {"query": value or None, "limit": 20 if subcmd == "list" else 10}
        )
        items = result["items"]
        if not items:
            await sink.text(
                tr("memory.list_empty" if subcmd == "list" else "memory.not_found", lang)
            )
            return
        lines = []
        if subcmd == "list":
            lines.append(tr("memory.list_header", lang, count=len(items)))
        for item in items:
            line = f"- [{item['id']}] {item['assertion']}"
            if item.get("entity_name"):
                line += tr("memory.topic_suffix", lang, topic=item["entity_name"])
            lines.append(line)
        await sink.text("\n".join(lines))
    except OctopError as exc:
        await sink.text(
            tr("memory.forbidden", lang) if exc.code == ErrorCode.FORBIDDEN else exc.message
        )


async def cmd_memory(d: SlashDispatcher, cmd: SlashCommand, ctx: SlashCtx, sink: SlashSink) -> None:
    lang = lang_of(ctx)
    # The IM fallback ID identifies the persistence owner, not the sender.
    if not ThreadRegistry.is_virtual_channel(ctx.channel_type):
        await sink.text(tr("memory.trusted_chat", lang))
        return
    row = (
        await asyncio.to_thread(ctx.user_repo.get, ctx.user_id)
        if ctx.user_repo is not None and ctx.user_id > 0
        else None
    )
    if row is None or row.disabled:
        await sink.text(full_tr("memory_slim.forbidden", lang))
        return
    args = cmd.args.split()
    if not args:
        await sink.text(tr("memory.usage", lang))
        return
    subcmd = args[0].lower()
    if subcmd == "explain":
        await sink.text(tr("memory.explain", lang))
        return
    if subcmd in {"save", "search", "query", "list", "forget"}:
        user = User(row.id, row.username, row.role, row.display_name, row.locale, row.permissions)
        value = cmd.args.strip()[len(subcmd) :].strip()
        await _personal_command(subcmd, value, ctx, user, sink)
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
