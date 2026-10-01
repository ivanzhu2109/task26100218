"""资源失活清理库的自动化测试。

运行：python3 -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime, timedelta, timezone

from cleanup import (
    CleanupService,
    Decision,
    ResourceStatus,
    ResourceType,
    TicketStatus,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


class CleanupServiceTest(unittest.TestCase):
    def setUp(self):
        self.svc = CleanupService(":memory:")

    def tearDown(self):
        self.svc.close()

    def register(self, resource_id="res-1", created_at=T0):
        return self.svc.register_resource(
            resource_id, ResourceType.SHAREPOINT_SITE, created_at
        )

    def open_tickets(self, resource_id="res-1"):
        return [
            t
            for t in self.svc.list_tickets(resource_id)
            if t.status in (TicketStatus.PENDING, TicketStatus.ESCALATED)
        ]

    # ---- 阈值与基准 ----

    def test_thresholds_are_inclusive_and_based_on_created_at(self):
        self.register()
        self.svc.evaluate(at(89))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.ACTIVE)
        self.svc.evaluate(at(90))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)
        self.svc.evaluate(at(269))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)
        self.svc.evaluate(at(270))
        resource = self.svc.get_resource("res-1")
        self.assertEqual(resource.status, ResourceStatus.PENDING_DELETION)
        self.assertEqual(resource.activity_baseline, T0)
        tickets = self.open_tickets()
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].status, TicketStatus.PENDING)
        self.assertEqual(tickets[0].pending_since, at(270))

    def test_latest_activity_becomes_baseline(self):
        self.register()
        self.svc.record_activity("res-1", at(10), now=at(10))
        self.assertEqual(self.svc.get_resource("res-1").activity_baseline, at(10))
        self.svc.evaluate(at(10 + 90))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)

    def test_older_or_duplicate_activity_does_not_move_baseline(self):
        self.register()
        self.svc.record_activity("res-1", at(50), now=at(50))
        self.svc.record_activity("res-1", at(20), now=at(60))
        self.svc.record_activity("res-1", at(50), now=at(61))
        resource = self.svc.get_resource("res-1")
        self.assertEqual(resource.activity_baseline, at(50))
        self.assertEqual(resource.cycle, 2)
        self.assertEqual(self.svc.list_tickets("res-1"), [])

    # ---- 升级计时 ----

    def test_escalation_counts_from_pending_since_not_discovery(self):
        self.register()
        self.svc.evaluate(at(270))
        self.assertEqual(self.open_tickets()[0].status, TicketStatus.PENDING)
        self.svc.evaluate(at(283))
        self.assertEqual(self.open_tickets()[0].status, TicketStatus.PENDING)
        self.svc.evaluate(at(284))
        self.assertEqual(self.open_tickets()[0].status, TicketStatus.ESCALATED)

    def test_single_evaluation_jumping_all_thresholds_yields_escalated_ticket(self):
        self.register()
        self.svc.evaluate(at(300))
        resource = self.svc.get_resource("res-1")
        self.assertEqual(resource.status, ResourceStatus.PENDING_DELETION)
        tickets = self.svc.list_tickets("res-1")
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].status, TicketStatus.ESCALATED)
        self.assertEqual(tickets[0].pending_since, at(270))

    def test_repeated_evaluation_does_not_duplicate_tickets(self):
        self.register()
        for day in (270, 271, 280, 285, 400):
            self.svc.evaluate(at(day))
        self.assertEqual(len(self.svc.list_tickets("res-1")), 1)
        self.assertEqual(self.open_tickets()[0].status, TicketStatus.ESCALATED)

    # ---- 补录活动与新周期 ----

    def test_backfilled_activity_invalidates_old_ticket_and_starts_new_cycle(self):
        self.register()
        self.svc.evaluate(at(270))
        old_ticket = self.open_tickets()[0]
        self.assertEqual(old_ticket.cycle, 1)

        # 补录一条第 10 天的活动（晚于创建时间，基准前移，开启新周期）
        self.svc.record_activity("res-1", at(10), now=at(300))

        resource = self.svc.get_resource("res-1")
        self.assertEqual(resource.cycle, 2)
        self.assertEqual(resource.activity_baseline, at(10))
        # 新基准第 270 天为 at(280)，已过；且已过升级点 at(294)
        self.assertEqual(resource.status, ResourceStatus.PENDING_DELETION)

        tickets = self.svc.list_tickets("res-1")
        self.assertEqual(len(tickets), 2)
        old = self.svc.list_tickets("res-1")[0]
        self.assertEqual(old.status, TicketStatus.WITHDRAWN)
        new = self.open_tickets()
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].cycle, 2)
        self.assertEqual(new[0].status, TicketStatus.ESCALATED)
        self.assertEqual(new[0].pending_since, at(280))

        # 重复评估不再生成新工单
        self.svc.evaluate(at(301))
        self.assertEqual(len(self.svc.list_tickets("res-1")), 2)

    def test_backfilled_activity_restoring_activity_prevents_deletion(self):
        self.register()
        self.svc.evaluate(at(270))
        # 补录一条第 200 天的活动：新基准第 270 天在 at(470)，当前不欠删除
        self.svc.record_activity("res-1", at(200), now=at(300))
        resource = self.svc.get_resource("res-1")
        self.assertEqual(resource.status, ResourceStatus.INACTIVE)
        self.assertEqual(self.open_tickets(), [])
        self.assertEqual(
            self.svc.list_tickets("res-1")[0].status, TicketStatus.WITHDRAWN
        )

    # ---- 裁决 ----

    def test_reject_then_reevaluate_keeps_inactive_and_single_ticket(self):
        self.register()
        self.svc.evaluate(at(270))
        ticket = self.open_tickets()[0]
        self.svc.decide(ticket.ticket_id, Decision.REJECT, now=at(271))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)
        # 拒绝后的重复评估：状态保持失活，本周期不再生成工单
        for day in (280, 300, 500):
            self.svc.evaluate(at(day))
            self.assertEqual(
                self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE
            )
            self.assertEqual(len(self.svc.list_tickets("res-1")), 1)
        # 新周期才可重新触发
        self.svc.record_activity("res-1", at(500), now=at(500))
        self.svc.evaluate(at(500 + 270))
        self.assertEqual(
            self.svc.get_resource("res-1").status, ResourceStatus.PENDING_DELETION
        )
        self.assertEqual(len(self.svc.list_tickets("res-1")), 2)
        self.assertEqual(len(self.open_tickets()), 1)

    def test_approve_is_terminal_and_activity_cannot_revive(self):
        self.register()
        self.svc.evaluate(at(270))
        ticket = self.open_tickets()[0]
        self.svc.decide(ticket.ticket_id, Decision.APPROVE, now=at(271))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.DELETED)
        self.svc.record_activity("res-1", at(400), now=at(400))
        self.svc.evaluate(at(500))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.DELETED)
        self.assertEqual(len(self.svc.list_tickets("res-1")), 1)

    def test_duplicate_and_conflicting_decisions_have_no_effect(self):
        self.register()
        self.svc.evaluate(at(270))
        ticket = self.open_tickets()[0]
        self.svc.decide(ticket.ticket_id, Decision.REJECT, now=at(271))
        # 重复相同裁决：幂等
        again = self.svc.decide(ticket.ticket_id, Decision.REJECT, now=at(272))
        self.assertEqual(again.status, TicketStatus.REJECTED)
        self.assertEqual(again.decided_at, at(271))
        # 相反裁决：不得改变资源
        self.svc.decide(ticket.ticket_id, Decision.APPROVE, now=at(273))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)
        self.assertEqual(
            self.svc.list_tickets("res-1")[0].status, TicketStatus.REJECTED
        )

    def test_decision_on_withdrawn_ticket_has_no_effect(self):
        self.register()
        self.svc.evaluate(at(270))
        old_ticket = self.open_tickets()[0]
        self.svc.record_activity("res-1", at(200), now=at(300))
        result = self.svc.decide(old_ticket.ticket_id, Decision.APPROVE, now=at(301))
        self.assertEqual(result.status, TicketStatus.WITHDRAWN)
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.INACTIVE)

    def test_decision_uses_current_baseline_not_stale_ticket(self):
        self.register()
        self.svc.evaluate(at(270))
        old_ticket = self.open_tickets()[0]
        # 基准前移后旧工单被撤回，针对它的批准不得删除资源
        self.svc.record_activity("res-1", at(10), now=at(300))
        self.svc.decide(old_ticket.ticket_id, Decision.APPROVE, now=at(301))
        self.assertNotEqual(
            self.svc.get_resource("res-1").status, ResourceStatus.DELETED
        )
        # 当前周期的新工单可以正常裁决
        new_ticket = self.open_tickets()[0]
        self.svc.decide(new_ticket.ticket_id, Decision.APPROVE, now=at(302))
        self.assertEqual(self.svc.get_resource("res-1").status, ResourceStatus.DELETED)

    # ---- 查询 ----

    def test_query_resource_and_tickets(self):
        self.register("res-a")
        self.register("res-b")
        self.svc.evaluate(at(270))
        view = self.svc.get_resource("res-a")
        self.assertEqual(view.resource_type, ResourceType.SHAREPOINT_SITE)
        self.assertEqual(view.created_at, T0)
        self.assertEqual(len(self.svc.list_tickets("res-a")), 1)
        self.assertEqual(len(self.svc.list_tickets("res-b")), 1)
        self.assertEqual(len(self.svc.list_tickets()), 2)

    def test_register_is_idempotent_and_created_at_is_fixed(self):
        self.register()
        self.register()
        with self.assertRaises(ValueError):
            self.svc.register_resource(
                "res-1", ResourceType.SHAREPOINT_SITE, at(5)
            )


if __name__ == "__main__":
    unittest.main()
