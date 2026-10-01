"""业务逻辑层：资源失活清理服务。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .models import (
    ESCALATE_AFTER,
    INACTIVE_AFTER,
    PENDING_DELETE_AFTER,
    Resource,
    ResourceState,
    Ticket,
    TicketStatus,
)
from .store import Store

_OPEN_STATUSES = (TicketStatus.PENDING, TicketStatus.ESCALATED)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CleanupService:
    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "CleanupService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def register_resource(self, resource_id: str, kind: str, created_at: datetime) -> Resource:
        created_at = _utc(created_at)
        resource = Resource(
            id=resource_id,
            kind=kind,
            created_at=created_at,
            baseline=created_at,
            cycle=1,
            state=ResourceState.ACTIVE,
            cycle_rejected=False,
        )
        self._store.insert_resource(resource)
        return resource

    def record_activity(self, resource_id: str, activity_time: datetime, now: datetime) -> Resource:
        activity_time = _utc(activity_time)
        now = _utc(now)
        resource = self._require_resource(resource_id)
        self._store.insert_activity(resource_id, activity_time, now)
        if resource.state == ResourceState.DELETED:
            return resource
        if activity_time > resource.baseline:
            self._withdraw_open_tickets(resource)
            resource = replace(
                resource,
                baseline=activity_time,
                cycle=resource.cycle + 1,
                cycle_rejected=False,
            )
        return self._sync(resource, now)

    def evaluate(self, now: datetime) -> list[Resource]:
        now = _utc(now)
        return [self._sync(resource, now) for resource in self._store.list_resources()]

    def decide(self, ticket_id: int, approve: bool, now: datetime) -> Ticket | None:
        now = _utc(now)
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            return None
        resource = self._store.get_resource(ticket.resource_id)
        if resource is None:
            return ticket
        if ticket.status not in _OPEN_STATUSES:
            return ticket
        if ticket.cycle != resource.cycle or resource.state == ResourceState.DELETED:
            return ticket
        if approve:
            ticket = replace(ticket, status=TicketStatus.APPROVED, decided_at=now)
            resource = replace(resource, state=ResourceState.DELETED)
        else:
            ticket = replace(ticket, status=TicketStatus.REJECTED, decided_at=now)
            resource = replace(resource, state=ResourceState.INACTIVE, cycle_rejected=True)
        self._store.update_ticket(ticket)
        self._store.update_resource(resource)
        return ticket

    def get_resource_with_tickets(self, resource_id: str) -> tuple[Resource, list[Ticket]] | None:
        resource = self._store.get_resource(resource_id)
        if resource is None:
            return None
        return resource, self._store.list_tickets(resource_id)

    def _require_resource(self, resource_id: str) -> Resource:
        resource = self._store.get_resource(resource_id)
        if resource is None:
            raise KeyError(f"unknown resource: {resource_id}")
        return resource

    def _withdraw_open_tickets(self, resource: Resource) -> None:
        for ticket in self._store.list_tickets(resource.id):
            if ticket.status in _OPEN_STATUSES:
                self._store.update_ticket(replace(ticket, status=TicketStatus.WITHDRAWN))

    def _sync(self, resource: Resource, now: datetime) -> Resource:
        if resource.state == ResourceState.DELETED:
            return resource
        pending_since = resource.baseline + PENDING_DELETE_AFTER
        if now >= pending_since and not resource.cycle_rejected:
            resource = replace(resource, state=ResourceState.PENDING_DELETE)
            self._store.update_resource(resource)
            self._ensure_ticket(resource, pending_since, now)
        elif now >= resource.baseline + INACTIVE_AFTER:
            resource = replace(resource, state=ResourceState.INACTIVE)
            self._store.update_resource(resource)
        else:
            resource = replace(resource, state=ResourceState.ACTIVE)
            self._store.update_resource(resource)
        return resource

    def _ensure_ticket(self, resource: Resource, pending_since: datetime, now: datetime) -> None:
        escalated = now >= pending_since + ESCALATE_AFTER
        ticket = self._store.open_ticket_for_cycle(resource.id, resource.cycle)
        if ticket is None:
            self._store.insert_ticket(
                Ticket(
                    id=0,
                    resource_id=resource.id,
                    cycle=resource.cycle,
                    status=TicketStatus.ESCALATED if escalated else TicketStatus.PENDING,
                    pending_since=pending_since,
                    created_at=now,
                    decided_at=None,
                )
            )
        elif (
            escalated
            and ticket.status == TicketStatus.PENDING
        ):
            self._store.update_ticket(replace(ticket, status=TicketStatus.ESCALATED))
