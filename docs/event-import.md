# ConflictScope P2：审核事件导入规范

P2 采用独立于新闻聚合的人工审核流程。GDELT/RSS 报道不能直接自动升级成已确认的冲突、制裁或港口关闭事件，也不能据此推断价格因果关系。

## 使用

    python sync_market.py init
    python sync_events.py validate --file reviewed-events.json
    python sync_events.py import --file reviewed-events.json
    python sync_events.py list --country UKR
    python server.py

导入文件是本地 JSON；不会在导入时访问网络。数据位于 Git 忽略的 data/geoeconomics.sqlite3 中。

## 批次数据契约

顶层字段：format 必须等于 conflictscope.events.v1；sources 为 1–100 个注册来源；events 为 1–200 个待导入事件。

每个来源包含 id、name、homepage_url（HTTPS）、access_type（public / registration / restricted）、可选的 license_note。来源 ID 在批次内不可重复，已登记来源的名称、网址、访问级别也不可静默更改。

每条事件包括：
- id、title、event_type、verification_status、source_id、source_url、description。
- occurred_at：事件发生日；announced_at：公告日；effective_at：措施生效日；published_at：材料发表日。未知字段应填 null，至少一项存在。若 date_precision 为 day，使用真实的 YYYY-MM-DD 日期；小时/分钟时间应携带时区。
- review：decision 必须为 approved，包含 reviewer、reviewed_at 和可选 note。审核批准是编辑程序通过，不代表独立证明事件真实性。
- evidence：至少一条 role=primary 的 HTTPS 原始证据；可以另外收录 corroborating 或 context 证据；每条记录包括 source_id、url，可附 published_at、note。source_url 必须包含在 evidence URL 中。
- links：至少一个 country_id、commodity_id 或 infrastructure_label 关联，标注 impact_mechanism（supply、demand、logistics、sanctions、expectations、other 或 undetermined）、evidence_url 和可选的 evidence_note；evidence_url 必须属于当前事件的证据清单。

完整可执行的**虚构测试用例**参见 tests/test_events.py 中的 fixture()。它只用于验证导入结构，example.org URL 不代表真实政策或冲突，严禁当作真实历史事件填充生产库。

## 审核原则

1. 优先审阅原始制裁法令、官方政府公告、联合国资料、港口或交通主管部门公告。检查适用司法辖区、受影响商品/交通线路和政策正式生效日。
2. 来源发布日期与生效日必须分别存储；没有可信证据的字段保留为空，不可以根据新闻推测。
3. 对同一事实的报道、多方核实和正式官方材料使用不同的 verification_status，由编辑人员判断。编审状态与真实性判断应区别对待。
4. 产品显示相关商品和贸易传导机制时，只视为研究假设，不代表已经证明某项事件造成了价格涨跌。
5. 同一事件 ID 和完全相同记录可重复导入；如果同 ID 的内容发生变化，导入会拒绝，不会修改已审计记录。必要修订应使用新 ID 并在人工作业流程中注明缘由。
6. 数据包事务写入，批次中任一事件无效，全部新事件都不写入。未经事件审核表批准的旧记录，不会出现在时间轴查询中。
7. 真实事件数据库初始为空；本阶段不内置虚构历史事件，也不自动导入 UCDP/ACLED 的自动编码记录。

## 查询

- GET /api/timeline?country=UKR&basis=impact
- GET /api/events?country=UKR&commodity=wheat&type=embargo&start=2024-01-01&end=2026-10-15
- basis 可为 impact / occurred / announced / effective / published。
- impact 模式下，制裁、禁运及贸易政策优先生效日期；其他类型优先实际发生日期。缺失时采用有记录的其他日期，并保留所有原始日期以供审计。

本阶段输出的是可追溯的多轨道事件记录。冲突烈度统计、事件窗口异常收益及因果评估属于后续阶段，不能用当前时间轴结果冒充统计结论。
