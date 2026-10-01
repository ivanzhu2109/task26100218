import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from resource_cleanup import CleanupService, ResourceState, TicketStatus

UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)


class CleanupTestBase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(self.db_path)
        self.svc = CleanupService(self.db_path)

    def tearDown(self):
        self.svc.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def register(self, rid="res-1", kind="group", created_at=T0):
        return self.svc.register_resource(rid, kind, created_at)

    def resource(self, rid="res-1"):
        return self.svc.get_resource_with_tickets(rid)[0]

    def tickets(self, rid="res-1"):
        return self.svc.get_resource_with_tickets(rid)[1]


class TestThresholds(CleanupTestBase):
    def test_inactive_threshold_inclusive(self):
        self.register()
        self.svc.evaluate(T0 + 90 * DAY - timedelta(seconds=1))
        self.assertEqual(self.resource().state, ResourceState.ACTIVE)
        self.svc.evaluate(T0 + 90 * DAY)
        self.assertEqual(self.resource().state, ResourceState.INACTIVE)

    def test_pending_delete_threshold_inclusive(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY - timedelta(seconds=1))
        self.assertEqual(self.resource().state, ResourceState.INACTIVE)
        self.assertEqual(self.tickets(), [])
        self.svc.evaluate(T0 + 270 * DAY)
        self.assertEqual(self.resource().state, ResourceState.PENDING_DELETE)
        self.assertEqual(len(self.tickets()), 1)

    def test_no_activity_uses_created_at_as_baseline(self):
        self.register(created_at=T0)
        self.assertEqual(self.resource().baseline, T0)


class TestEvaluation(CleanupTestBase):
    def test_single_evaluation_spanning_all_deadlines_escalates_immediately(self):
        self.register()
        # 首次评估已在第 284 天之后：直接呈现升级状态
        now = T0 + 300 * DAY
        self.svc.evaluate(now)
        self.assertEqual(self.resource().state, ResourceState.PENDING_DELETE)
        tickets = self.tickets()
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].status, TicketStatus.ESCALATED)
        # 工单计时从达到待删除条件的时刻（第 270 天）开始
        self.assertEqual(tickets[0].pending_since, T0 + 270 * DAY)

    def test_escalation_boundary_inclusive(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        ticket = self.tickets()[0]
        self.assertEqual(ticket.status, TicketStatus.PENDING)
        self.svc.evaluate(T0 + 284 * DAY - timedelta(seconds=1))
        self.assertEqual(self.tickets()[0].status, TicketStatus.PENDING)
        self.svc.evaluate(T0 + 284 * DAY)
        self.assertEqual(self.tickets()[0].status, TicketStatus.ESCALATED)

    def test_repeated_evaluation_does_not_duplicate_tickets(self):
        self.register()
        for day in (270, 271, 280, 290, 400):
            self.svc.evaluate(T0 + day * DAY)
        self.assertEqual(len(self.tickets()), 1)

    def test_repeated_evaluation_after_rejection_creates_no_ticket(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        ticket = self.tickets()[0]
        self.svc.decide(ticket.id, approve=False, now=T0 + 271 * DAY)
        self.assertEqual(self.resource().state, ResourceState.INACTIVE)
        # 拒绝后重复评估：保持失活，本周期不再生成工单
        for day in (272, 284, 300, 500):
            self.svc.evaluate(T0 + day * DAY)
            self.assertEqual(self.resource().state, ResourceState.INACTIVE)
            self.assertEqual(len(self.tickets()), 1)
        self.assertEqual(self.tickets()[0].status, TicketStatus.REJECTED)


class TestActivityAndCycles(CleanupTestBase):
    def test_backfilled_activity_still_pending_delete_new_cycle_single_ticket(self):
        self.register()
        # 第一周期进入待删除并生成工单
        self.svc.evaluate(T0 + 270 * DAY)
        old_ticket = self.tickets()[0]
        self.assertEqual(old_ticket.status, TicketStatus.PENDING)
        # 补录一条较旧但晚于基准的活动：新周期仍满足待删除条件
        activity_time = T0 + 100 * DAY
        now = T0 + 400 * DAY
        self.svc.record_activity("res-1", activity_time, now)
        resource = self.resource()
        self.assertEqual(resource.baseline, activity_time)
        self.assertEqual(resource.cycle, 2)
        self.assertEqual(resource.state, ResourceState.PENDING_DELETE)
        tickets = self.tickets()
        self.assertEqual(len(tickets), 2)
        old = next(t for t in tickets if t.id == old_ticket.id)
        new = next(t for t in tickets if t.id != old_ticket.id)
        # 旧工单立即撤回，新周期仅有一张工单
        self.assertEqual(old.status, TicketStatus.WITHDRAWN)
        self.assertIn(new.status, (TicketStatus.PENDING, TicketStatus.ESCALATED))
        self.assertEqual(new.cycle, 2)
        self.assertEqual(new.pending_since, activity_time + 270 * DAY)
        # 新周期再次评估不会重复生成
        self.svc.evaluate(now + 10 * DAY)
        self.assertEqual(len(self.tickets()), 2)

    def test_recovered_resource_not_deleted_by_old_ticket(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        old_ticket = self.tickets()[0]
        # 资源恢复活动（新基准很近），旧工单被撤回
        now = T0 + 280 * DAY
        self.svc.record_activity("res-1", now, now)
        self.assertEqual(self.resource().state, ResourceState.ACTIVE)
        # 针对撤回工单的批准不得删除资源
        self.svc.decide(old_ticket.id, approve=True, now=now + DAY)
        self.assertEqual(self.resource().state, ResourceState.ACTIVE)
        self.assertEqual(self.tickets()[0].status, TicketStatus.WITHDRAWN)

    def test_older_or_duplicate_activity_does_not_move_baseline_or_open_cycle(self):
        self.register()
        self.svc.record_activity("res-1", T0 + 50 * DAY, T0 + 50 * DAY)
        cycle = self.resource().cycle
        self.svc.record_activity("res-1", T0 + 50 * DAY, T0 + 60 * DAY)
        self.svc.record_activity("res-1", T0 + 30 * DAY, T0 + 60 * DAY)
        resource = self.resource()
        self.assertEqual(resource.baseline, T0 + 50 * DAY)
        self.assertEqual(resource.cycle, cycle)

    def test_new_cycle_after_rejection_can_retrigger(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        ticket = self.tickets()[0]
        self.svc.decide(ticket.id, approve=False, now=T0 + 271 * DAY)
        # 新活动开启新周期后可重新触发工单
        self.svc.record_activity("res-1", T0 + 280 * DAY, T0 + 280 * DAY)
        self.svc.evaluate(T0 + 280 * DAY + 270 * DAY)
        tickets = self.tickets()
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[-1].status, TicketStatus.PENDING)
        self.assertEqual(self.resource().state, ResourceState.PENDING_DELETE)


class TestDecision(CleanupTestBase):
    def _pending_ticket(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        return self.tickets()[0]

    def test_approve_deletes_resource_and_is_terminal(self):
        ticket = self._pending_ticket()
        self.svc.decide(ticket.id, approve=True, now=T0 + 271 * DAY)
        self.assertEqual(self.resource().state, ResourceState.DELETED)
        # 后续活动写入不得复活资源
        self.svc.record_activity("res-1", T0 + 300 * DAY, T0 + 300 * DAY)
        self.assertEqual(self.resource().state, ResourceState.DELETED)
        self.svc.evaluate(T0 + 600 * DAY)
        self.assertEqual(self.resource().state, ResourceState.DELETED)
        self.assertEqual(len(self.tickets()), 1)

    def test_duplicate_and_opposite_decisions_have_no_effect(self):
        ticket = self._pending_ticket()
        self.svc.decide(ticket.id, approve=False, now=T0 + 271 * DAY)
        # 重复相同裁决
        self.svc.decide(ticket.id, approve=False, now=T0 + 272 * DAY)
        self.assertEqual(self.tickets()[0].status, TicketStatus.REJECTED)
        self.assertEqual(self.tickets()[0].decided_at, T0 + 271 * DAY)
        # 相反裁决不得改变资源
        self.svc.decide(ticket.id, approve=True, now=T0 + 273 * DAY)
        self.assertEqual(self.resource().state, ResourceState.INACTIVE)
        self.assertEqual(self.tickets()[0].status, TicketStatus.REJECTED)

    def test_decision_uses_current_baseline_not_stale_ticket(self):
        ticket = self._pending_ticket()
        # 基准前移后旧工单已撤回，裁决不生效
        now = T0 + 280 * DAY
        self.svc.record_activity("res-1", now, now)
        result = self.svc.decide(ticket.id, approve=True, now=now + DAY)
        self.assertEqual(result.status, TicketStatus.WITHDRAWN)
        self.assertNotEqual(self.resource().state, ResourceState.DELETED)

    def test_unknown_ticket_returns_none(self):
        self.assertIsNone(self.svc.decide(9999, approve=True, now=T0))


class TestQuery(CleanupTestBase):
    def test_query_resource_with_tickets(self):
        self.register(rid="sp-site", kind="sharepoint_site")
        self.svc.evaluate(T0 + 270 * DAY)
        resource, tickets = self.svc.get_resource_with_tickets("sp-site")
        self.assertEqual(resource.id, "sp-site")
        self.assertEqual(resource.kind, "sharepoint_site")
        self.assertEqual(len(tickets), 1)
        self.assertIsNone(self.svc.get_resource_with_tickets("missing"))

    def test_persistence_across_reopen(self):
        self.register()
        self.svc.evaluate(T0 + 270 * DAY)
        self.svc.close()
        self.svc = CleanupService(self.db_path)
        resource, tickets = self.svc.get_resource_with_tickets("res-1")
        self.assertEqual(resource.state, ResourceState.PENDING_DELETE)
        self.assertEqual(len(tickets), 1)


if __name__ == "__main__":
    unittest.main()
