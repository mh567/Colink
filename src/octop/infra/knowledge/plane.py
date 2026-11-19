"""Choose knowledge file and vector storage from the control-plane driver."""

from __future__ import annotations

from typing import Any, cast

from octop.config import DatabaseConfig
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.pg_index import PgVectorKnowledgeIndex


def postgres_database(source: Any) -> DatabaseConfig | None:
    """Return the PostgreSQL config when *source* is a PG control plane.

    *source* may be an ``OctopConfig``, a services object, or a server.
    SQLite (and missing config) returns ``None`` so callers keep local files.
    """
    config = getattr(source, "config", None)
    if config is None:
        services = getattr(source, "services", None)
        config = getattr(services, "config", None) if services is not None else None
    if config is None:
        config = getattr(source, "_config", None)
    database = getattr(config, "database", None) if config is not None else None
    if database is None and getattr(source, "is_postgresql", False):
        database = source
    if database is None or not getattr(database, "is_postgresql", False):
        return None
    return cast(DatabaseConfig, database)


def open_knowledge_index(services: Any, kb_id: str) -> KnowledgeIndex | PgVectorKnowledgeIndex:
    """SQLite sidecar index, or pgvector when the control plane is PostgreSQL."""
    database = postgres_database(services)
    if database is None:
        return KnowledgeIndex(kb_id)
    return PgVectorKnowledgeIndex(kb_id, database)


def pgvector_available(services: Any) -> bool:
    """Check the operator-provided vector type without changing the database."""
    database = postgres_database(services)
    if database is None:
        return True
    import psycopg

    try:
        with psycopg.connect(database.postgresql_conninfo(), connect_timeout=3) as conn:
            return (
                conn.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'").fetchone()
                is not None
            )
    except psycopg.Error:
        return False
