"""SQLite 持久化层。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import Resource, ResourceState, Ticket, TicketStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    baseline TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    state TEXT NOT NULL,
    cycle_rejected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    activity_time TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    status TEXT NOT NULL,
    pending_since TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""


def _to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _row_to_resource(row: sqlite3.Row) -> Resource:
    return Resource(
        id=row["id"],
        kind=row["kind"],
        created_at=_to_dt(row["created_at"]),
        baseline=_to_dt(row["baseline"]),
        cycle=row["cycle"],
        state=ResourceState(row["state"]),
        cycle_rejected=bool(row["cycle_rejected"]),
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        resource_id=row["resource_id"],
        cycle=row["cycle"],
        status=TicketStatus(row["status"]),
        pending_since=_to_dt(row["pending_since"]),
        created_at=_to_dt(row["created_at"]),
        decided_at=_to_dt(row["decided_at"]),
    )


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def insert_resource(self, resource: Resource) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO resources (id, kind, created_at, baseline, cycle, state, cycle_rejected)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    resource.id,
                    resource.kind,
                    _to_str(resource.created_at),
                    _to_str(resource.baseline),
                    resource.cycle,
                    resource.state.value,
                    int(resource.cycle_rejected),
                ),
            )

    def get_resource(self, resource_id: str) -> Resource | None:
        row = self._conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        return _row_to_resource(row) if row else None

    def update_resource(self, resource: Resource) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE resources SET baseline = ?, cycle = ?, state = ?, cycle_rejected = ?"
                " WHERE id = ?",
                (
                    _to_str(resource.baseline),
                    resource.cycle,
                    resource.state.value,
                    int(resource.cycle_rejected),
                    resource.id,
                ),
            )

    def list_resources(self) -> list[Resource]:
        rows = self._conn.execute("SELECT * FROM resources ORDER BY id").fetchall()
        return [_row_to_resource(row) for row in rows]

    def insert_activity(self, resource_id: str, activity_time: datetime, received_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO activities (resource_id, activity_time, received_at) VALUES (?, ?, ?)",
                (resource_id, _to_str(activity_time), _to_str(received_at)),
            )

    def insert_ticket(self, ticket: Ticket) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO tickets (resource_id, cycle, status, pending_since, created_at, decided_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ticket.resource_id,
                    ticket.cycle,
                    ticket.status.value,
                    _to_str(ticket.pending_since),
                    _to_str(ticket.created_at),
                    _to_str(ticket.decided_at),
                ),
            )
        return cursor.lastrowid

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return _row_to_ticket(row) if row else None

    def update_ticket(self, ticket: Ticket) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE tickets SET status = ?, decided_at = ? WHERE id = ?",
                (ticket.status.value, _to_str(ticket.decided_at), ticket.id),
            )

    def list_tickets(self, resource_id: str) -> list[Ticket]:
        rows = self._conn.execute(
            "SELECT * FROM tickets WHERE resource_id = ? ORDER BY id", (resource_id,)
        ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def open_ticket_for_cycle(self, resource_id: str, cycle: int) -> Ticket | None:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE resource_id = ? AND cycle = ? AND status IN (?, ?)",
            (resource_id, cycle, TicketStatus.PENDING.value, TicketStatus.ESCALATED.value),
        ).fetchone()
        return _row_to_ticket(row) if row else None
