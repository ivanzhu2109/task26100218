"""类型定义与常量。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

INACTIVE_AFTER = timedelta(days=90)
PENDING_DELETE_AFTER = timedelta(days=270)
ESCALATE_AFTER = timedelta(days=14)


class ResourceState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING_DELETE = "pending_delete"
    DELETED = "deleted"


class TicketStatus(str, Enum):
    PENDING = "pending"
    ESCALATED = "escalated"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class Resource:
    id: str
    kind: str
    created_at: datetime
    baseline: datetime
    cycle: int
    state: ResourceState
    cycle_rejected: bool


@dataclass(frozen=True)
class Ticket:
    id: int
    resource_id: str
    cycle: int
    status: TicketStatus
    pending_since: datetime
    created_at: datetime
    decided_at: datetime | None
