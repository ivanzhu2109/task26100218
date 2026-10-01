# task26100218-b：资源失活清理库

用 Python + SQLite 实现的资源失活清理库，由上层直接调用，无接口层。
资源与工单持久化到本地 SQLite 文件，适用于清理长期闲置的 Groups、Teams
和 SharePoint 站点。

## 功能

- **资源活动写入** `record_activity`：补录活动记录；活动基准严格前移时开启
  新清理周期，旧周期未裁决工单立即撤回，按新基准与当前业务时刻重新判定。
- **全量策略评估** `evaluate`：对全部资源按当前业务时刻重算状态，生成或
  升级工单；可跳过任意日期，一次评估即达到当前应有状态。
- **工单裁决** `decide`：仅当前周期有效的待处理/升级工单可裁决；批准使
  资源进入终态已删除，拒绝使资源回到失活且本周期不再生成工单。
- **资源及关联工单查询** `get_resource_with_tickets`。

## 时间规则

- 无活动记录时以创建时间为活动基准，否则取已收到的最新活动时间；
  重复或更旧的活动不会使基准倒退，也不开启新周期。
- 距基准满 90 天失活，连续失活满 180 天（即基准后第 270 天）进入待删除
  并生成一张待处理工单；阈值含等号，1 天为 24 小时。
- 每周期最多一张工单；工单计时从达到待删除条件的时刻开始，未裁决满
  14 天自动升级。
- 已删除为终态，后续活动写入不得复活；重复/相反/针对撤回工单的裁决均
  不重复生效。
- 业务时刻由上层以 UTC 提供，向前推进。

## 使用示例

```python
from datetime import datetime, timezone
from resource_cleanup import CleanupService

svc = CleanupService("cleanup.sqlite3")
now = datetime.now(timezone.utc)
svc.register_resource("team-42", "team", created_at=now)
svc.record_activity("team-42", activity_time=now, now=now)
svc.evaluate(now)
resource, tickets = svc.get_resource_with_tickets("team-42")
if tickets:
    svc.decide(tickets[-1].id, approve=False, now=now)
svc.close()
```

## 运行测试

无第三方依赖，Python 3.10+：

```bash
python3 -m unittest discover -s tests -v
```
