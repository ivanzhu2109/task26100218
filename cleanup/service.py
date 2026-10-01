"""业务逻辑层：活动写入、全量评估、工单裁决、查询。"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    ESCALATE_AFTER,
    INACTIVE_AFTER,
    PENDING_DELETION_AFTER,
    Decision,
    ResourceStatus,
    ResourceType,
    ResourceView,
    TicketStatus,
    TicketView,
)
from .store import Store


def _to_epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def _from_epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _resource_view(row: dict) -> ResourceView:
    return ResourceView(
        resource_id=row["resource_id"],
        resource_type=ResourceType(row["resource_type"]),
        created_at=_from_epoch(row["created_at"]),
        activity_baseline=_from_epoch(row["baseline"]),
        status=ResourceStatus(row["status"]),
        cycle=row["cycle"],
    )


def _ticket_view(row: dict) -> TicketView:
    decided_at = row["decided_at"]
    return TicketView(
        ticket_id=row["ticket_id"],
        resource_id=row["resource_id"],
        cycle=row["cycle"],
        status=TicketStatus(row["status"]),
        pending_since=_from_epoch(row["pending_since"]),
        created_at=_from_epoch(row["created_at"]),
        decided_at=_from_epoch(decided_at) if decided_at is not None else None,
    )


class CleanupService:
    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)

    def close(self) -> None:
        self._store.close()

    # ---- 资源登记（资源编号与创建时间固定） ----

    def register_resource(self, resource_id: str, resource_type, created_at: datetime):
        existing = self._store.get_resource(resource_id)
        if existing is not None:
            if existing["created_at"] != _to_epoch(created_at):
                raise ValueError(
                    f"resource {resource_id!r} already registered with a"
                    " different created_at"
                )
            return _resource_view(existing)
        rtype = resource_type.value if isinstance(resource_type, ResourceType) else str(resource_type)
        self._store.insert_resource(resource_id, rtype, _to_epoch(created_at))
        return _resource_view(self._store.get_resource(resource_id))

    # ---- 1. 资源活动写入 ----

    def record_activity(self, resource_id: str, activity_time: datetime, now: datetime):
        resource = self._require_resource(resource_id)
        activity_epoch = _to_epoch(activity_time)
        self._store.add_activity(resource_id, activity_epoch, _to_epoch(now))
        if resource["status"] == ResourceStatus.DELETED.value:
            return _resource_view(self._store.get_resource(resource_id))
        if activity_epoch > resource["baseline"]:
            old_cycle = resource["cycle"]
            self._store.withdraw_open_tickets(resource_id, old_cycle)
            self._store.update_resource(
                resource_id,
                baseline=activity_epoch,
                cycle=old_cycle + 1,
                rejected_cycle=None,
            )
            self._evaluate_resource(resource_id, _to_epoch(now))
        return _resource_view(self._store.get_resource(resource_id))

    # ---- 2. 全量策略评估 ----

    def evaluate(self, now: datetime):
        now_epoch = _to_epoch(now)
        for row in self._store.list_resources():
            if row["status"] == ResourceStatus.DELETED.value:
                continue
            self._evaluate_resource(row["resource_id"], now_epoch)
        return [self.get_resource(r["resource_id"]) for r in self._store.list_resources()]

    def _evaluate_resource(self, resource_id: str, now_epoch: float) -> None:
        resource = self._store.get_resource(resource_id)
        baseline = resource["baseline"]
        cycle = resource["cycle"]
        inactive_at = baseline + INACTIVE_AFTER.total_seconds()
        pending_at = baseline + PENDING_DELETION_AFTER.total_seconds()

        if resource["rejected_cycle"] == cycle:
            self._store.update_resource(
                resource_id, status=ResourceStatus.INACTIVE.value
            )
            return

        if now_epoch >= pending_at:
            self._store.update_resource(
                resource_id, status=ResourceStatus.PENDING_DELETION.value
            )
            escalate_at = pending_at + ESCALATE_AFTER.total_seconds()
            ticket = self._store.get_cycle_ticket(resource_id, cycle)
            if ticket is None:
                status = (
                    TicketStatus.ESCALATED.value
                    if now_epoch >= escalate_at
                    else TicketStatus.PENDING.value
                )
                self._store.insert_ticket(
                    resource_id, cycle, status, pending_at, now_epoch
                )
            elif ticket["status"] == TicketStatus.PENDING.value and now_epoch >= escalate_at:
                self._store.update_ticket_status(
                    ticket["ticket_id"], TicketStatus.ESCALATED.value
                )
        elif now_epoch >= inactive_at:
            self._store.update_resource(
                resource_id, status=ResourceStatus.INACTIVE.value
            )
        else:
            self._store.update_resource(
                resource_id, status=ResourceStatus.ACTIVE.value
            )

    # ---- 3. 工单裁决 ----

    def decide(self, ticket_id: int, decision: Decision, now: datetime):
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(f"ticket {ticket_id} not found")
        decision = Decision(decision)
        if ticket["status"] not in (TicketStatus.PENDING.value, TicketStatus.ESCALATED.value):
            return _ticket_view(ticket)
        resource = self._require_resource(ticket["resource_id"])
        if resource["status"] == ResourceStatus.DELETED.value:
            return _ticket_view(ticket)
        if ticket["cycle"] != resource["cycle"]:
            return _ticket_view(ticket)
        now_epoch = _to_epoch(now)
        if decision is Decision.APPROVE:
            self._store.update_ticket_status(
                ticket_id, TicketStatus.APPROVED.value, decided_at=now_epoch
            )
            self._store.update_resource(
                ticket["resource_id"], status=ResourceStatus.DELETED.value
            )
        else:
            self._store.update_ticket_status(
                ticket_id, TicketStatus.REJECTED.value, decided_at=now_epoch
            )
            self._store.update_resource(
                ticket["resource_id"],
                status=ResourceStatus.INACTIVE.value,
                rejected_cycle=resource["cycle"],
            )
        return _ticket_view(self._store.get_ticket(ticket_id))

    # ---- 4. 资源及关联工单查询 ----

    def get_resource(self, resource_id: str):
        return _resource_view(self._require_resource(resource_id))

    def list_tickets(self, resource_id: str | None = None):
        return [_ticket_view(r) for r in self._store.list_tickets(resource_id)]

    # ---- 内部 ----

    def _require_resource(self, resource_id: str) -> dict:
        resource = self._store.get_resource(resource_id)
        if resource is None:
            raise KeyError(f"resource {resource_id!r} not found")
        return resource
