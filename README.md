# 资源失活清理库

基于 Python 标准库 + SQLite 的本地资源失活清理库，由上层直接调用，无接口层、
无外部服务依赖。资源与工单持久化到本地 SQLite 文件。

## 功能

四类操作，均由 `cleanup.CleanupService` 提供：

- **资源活动写入** `record_activity(resource_id, activity_time, now)`
- **全量策略评估** `evaluate(now)`
- **工单裁决** `decide(ticket_id, decision, now)`
- **资源及关联工单查询** `get_resource(resource_id)` / `list_tickets(resource_id=None)`

## 规则摘要

- 活动基准 = max(创建时间, 已收到的最新活动时间)；重复或更旧的活动不回退基准。
- 基准满 90 天失活，连续失活满 180 天（基准后第 270 天）进入待删除并生成一张工单，
  阈值含等号，1 天 = 24 小时。
- 基准严格前移即开启新清理周期：旧周期未裁决工单立即撤回，已裁决工单保留结论；
  写入后按新基准与当前业务时刻立即重新判定。
- 每个周期最多一张工单；升级计时从该周期达到待删除条件的时刻（基准 + 270 天）起算，
  满 14 天自动升级，与评估发现时刻无关。
- 仅当前周期的待处理/升级工单可裁决：批准 → 已删除（终态，不可复活）；
  拒绝 → 回到失活且本周期不再生成工单。重复/相反/针对撤回工单的裁决均不改变资源。
- 业务时刻由上层以 UTC `datetime` 提供并保证向前推进。

## 使用示例

```python
from datetime import datetime, timezone
from cleanup import CleanupService, Decision, ResourceType

svc = CleanupService("cleanup.db")  # 本地 SQLite 文件；":memory:" 亦可
t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

svc.register_resource("site-1", ResourceType.SHAREPOINT_SITE, t0)
svc.record_activity("site-1", activity_time=t0, now=t0)
svc.evaluate(now=t0)                       # 全量评估
for ticket in svc.list_tickets("site-1"):  # 查询工单
    svc.decide(ticket.ticket_id, Decision.APPROVE, now=t0)
print(svc.get_resource("site-1"))          # 查询资源
svc.close()
```

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

仅依赖 Python 3.10+ 标准库，无需安装任何第三方包。

## 代码结构

- `cleanup/models.py` — 状态枚举、阈值常量、查询视图对象
- `cleanup/store.py` — SQLite 持久化层（resources / activities / tickets 三表）
- `cleanup/service.py` — 业务逻辑层（四类操作与清理周期状态机）
- `tests/test_cleanup.py` — 自动化测试（15 个用例）
