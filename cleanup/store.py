"""SQLite 持久化层：资源、活动记录、工单三张表。

所有时间戳以 Unix epoch 秒（REAL）存储，UTC 语义由上层保证。
"""

from __future__ import annotations

import sqlite3

_OPEN_TICKET_STATUSES = ("pending", "escalated")


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resources (
                resource_id    TEXT PRIMARY KEY,
                resource_type  TEXT NOT NULL,
                created_at     REAL NOT NULL,
                baseline       REAL NOT NULL,
                status         TEXT NOT NULL,
                cycle          INTEGER NOT NULL,
                rejected_cycle INTEGER
            );
            CREATE TABLE IF NOT EXISTS activities (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id   TEXT NOT NULL REFERENCES resources(resource_id),
                activity_time REAL NOT NULL,
                recorded_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id   TEXT NOT NULL REFERENCES resources(resource_id),
                cycle         INTEGER NOT NULL,
                status        TEXT NOT NULL,
                pending_since REAL NOT NULL,
                created_at    REAL NOT NULL,
                decided_at    REAL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- 资源 ----

    def insert_resource(self, resource_id, resource_type, created_at):
        with self._conn:
            self._conn.execute(
                "INSERT INTO resources (resource_id, resource_type, created_at,"
                " baseline, status, cycle, rejected_cycle)"
                " VALUES (?, ?, ?, ?, 'active', 1, NULL)",
                (resource_id, resource_type, created_at, created_at),
            )

    def get_resource(self, resource_id):
        row = self._conn.execute(
            "SELECT * FROM resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_resources(self):
        rows = self._conn.execute(
            "SELECT * FROM resources ORDER BY resource_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_resource(self, resource_id, *, baseline=None, status=None,
                        cycle=None, rejected_cycle="KEEP"):
        sets, params = [], []
        if baseline is not None:
            sets.append("baseline = ?")
            params.append(baseline)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if cycle is not None:
            sets.append("cycle = ?")
            params.append(cycle)
        if rejected_cycle != "KEEP":
            sets.append("rejected_cycle = ?")
            params.append(rejected_cycle)
        if not sets:
            return
        params.append(resource_id)
        with self._conn:
            self._conn.execute(
                f"UPDATE resources SET {', '.join(sets)} WHERE resource_id = ?",
                params,
            )

    # ---- 活动 ----

    def add_activity(self, resource_id, activity_time, recorded_at):
        with self._conn:
            self._conn.execute(
                "INSERT INTO activities (resource_id, activity_time, recorded_at)"
                " VALUES (?, ?, ?)",
                (resource_id, activity_time, recorded_at),
            )

    # ---- 工单 ----

    def insert_ticket(self, resource_id, cycle, status, pending_since, created_at):
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO tickets (resource_id, cycle, status, pending_since,"
                " created_at, decided_at) VALUES (?, ?, ?, ?, ?, NULL)",
                (resource_id, cycle, status, pending_since, created_at),
            )
        return cur.lastrowid

    def get_ticket(self, ticket_id):
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_cycle_ticket(self, resource_id, cycle):
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE resource_id = ? AND cycle = ?",
            (resource_id, cycle),
        ).fetchone()
        return dict(row) if row else None

    def list_tickets(self, resource_id=None):
        if resource_id is None:
            rows = self._conn.execute(
                "SELECT * FROM tickets ORDER BY ticket_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tickets WHERE resource_id = ? ORDER BY ticket_id",
                (resource_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_ticket_status(self, ticket_id, status, decided_at=None):
        with self._conn:
            self._conn.execute(
                "UPDATE tickets SET status = ?, decided_at = ?"
                " WHERE ticket_id = ?",
                (status, decided_at, ticket_id),
            )

    def withdraw_open_tickets(self, resource_id, cycle):
        with self._conn:
            self._conn.execute(
                "UPDATE tickets SET status = 'withdrawn'"
                " WHERE resource_id = ? AND cycle = ?"
                " AND status IN ('pending', 'escalated')",
                (resource_id, cycle),
            )
