"""类型定义：资源、工单、状态枚举与视图对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

INACTIVE_AFTER = timedelta(days=90)
PENDING_DELETION_AFTER = timedelta(days=270)
ESCALATE_AFTER = timedelta(days=14)


class ResourceType(str, Enum):
    GROUP = "group"
    TEAM = "team"
    SHAREPOINT_SITE = "sharepoint_site"


class ResourceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING_DELETION = "pending_deletion"
    DELETED = "deleted"


class TicketStatus(str, Enum):
    PENDING = "pending"
    ESCALATED = "escalated"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Decision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class ResourceView:
    resource_id: str
    resource_type: ResourceType
    created_at: datetime
    activity_baseline: datetime
    status: ResourceStatus
    cycle: int


@dataclass(frozen=True)
class TicketView:
    ticket_id: int
    resource_id: str
    cycle: int
    status: TicketStatus
    pending_since: datetime
    created_at: datetime
    decided_at: datetime | None
