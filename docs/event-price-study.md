# P3：冲突与贸易事件 — 大宗商品价格事件窗口研究

研究目的：将 P2 的**有原始出处且有审查记录的事件**与 P1/历史回填的**真实价格序列**按时间对齐，观察事件前后价格变化；允许使用另一条日度现货序列做简化市场模型基准。**历史相关性不等于因果性，不构成投资建议。**

## 运行顺序（依赖 P0→P1→P2→历史价格）

    git fetch origin
    git switch feature/v3-p3-event-price-study
    python sync_market.py init
    python sync_events.py import --file data/official-events-2022-2024.json
    python historical_prices.py fred --series DCOILBRENTEU --start 2021-01-01 --end 2025-12-31
    python historical_prices.py fred --series DCOILWTICO --start 2021-01-01 --end 2025-12-31
    python historical_prices.py coverage --country RUS
    python server.py

打开 http://127.0.0.1:8765 ，选择俄罗斯（RUS），可查看与原油相关的官方来源事件、Brent/WTI 日度价格和 P3 事件分析。若任一远程源不可用，应在本地下载有效数据后再执行对应回填。不会用固定随机示例值替代真实价格。

## 新增 API

- GET /api/analysis/event-study?event_id=rus-2022-12-05-crude-price-cap&series=fred:DCOILBRENTEU&window=20&basis=impact
- GET /api/analysis/compare?country=RUS&series=fred:DCOILBRENTEU&window=20
- 对照模型示例：GET /api/analysis/event-study?event_id=rus-2022-12-05-crude-price-cap&series=fred:DCOILBRENTEU&control=fred:DCOILWTICO&window=20

返回带 provenance 的事件、日期字段、价格序列元信息、t=0 交易观测日期、价格基线日期、实际样本点和相对变动百分比。`status=insufficient_data` 或 `status=unsupported_frequency` 时没有编造的价格/收益率；缺失 horizon 的 `returns[k]` 为 null。

## 事件窗口的严格定义

1. `basis=impact`：制裁、禁运、贸易政策优先使用**生效日期**；其他事件优先**实际发生日**。可切换 occurred / announced / effective / published；请求字段缺失时有明示 `anchor_field` 与 `date_fallback`。
2. **t=0** = 事件日当天或之后第一个确有有效现货报价的日期；**基线价** = 事件实际日期之前最后一次有效报价。日期对齐的前后报价相隔均不得超过 7 个自然日。不能把周末、停市日填充为有报价的交易日。
3. t+1、t+5、t+20 等的 `k` 表示**后 k 个有效报价交易日**，不是自然日。任何内部连续报价缺口超过 7 个自然日会截断窗口，不跨越长时间数据缺口计算结果。
4. 对每个 horizon，累计简单价格变动 R_k = 100 × (P_{t+k} / P_{baseline} − 1)。这些是“事件前后累计变动”，**不是已证实的事件作用**，也不是异常收益；请勿称作事件造成的涨跌。
5. 默认窗口有 ±5、±10、±20、±30，范围内使用实际报价点绘图，不插值。价格基准保留币种、计价单位、数据源。**月度商品均价不具备日级事件反应分辨率**，因此返回 unsupported_frequency，而不是伪造日频响应。
6. 新闻、制裁公告一般只有日级日期。如果无法确认公告在市场收盘之前还是之后，事件研究只提供日期对齐，不会声称价格首次反应的精确时点。

## 可选的市场模型调整（CAR）

允许另外选择一条**不同 ID、同为日度现货且同币种**的价格基准（如 Brent 与 WTI）；仅在前期数据同时覆盖的日期，计算对数收益率，回归 `r_target = alpha + beta * r_control + residual`。

- 回归样本为目标价格系列事件日前 -90 至 -21 个有效交易日区间内，且控制价的对应日期与前一日期都有报价的配对收益率；至少 **30** 个配对样本，且控制价收益率具有非零变化。
- 估算的每个事件窗口 CAR_k = 100 × Σ residual_t（t=0...k）。单位为**累计异常对数收益百分点**，与简单收益率百分比不能无条件比较或相减。
- 无足够有效样本、控制序列不兼容、零方差或缺报价时，模型返回明确状态或 null，绝不显示虚假的 CAR。
- P3 没有实现 p 值、置信区间或自动因果结论。OLS 结果是选择特定对照序列后的**模型依赖描述**；同时发生的事件、美元/库存/天气/经济需求等因素均可能是混杂变量，不能以 CAR 推断政治或军事事件的独立影响。

## 跨事件对比

`/api/analysis/compare` 只检索同时与所选国家和所选商品存在明确关联的审查事件，最多 50 条；按事件时间排序并分别计算窗口。比较表不代表事件是随机分配的或统计上相互独立，时间窗口可能相互重叠。

## 已知限制 / 后续验收

- **P2 现有 11 条历史事件属于 AI 辅助来源核对，尚未独立人工复核。** P3 在每条事件元信息中继续展示这一状态。
- **P3 依赖先前明确执行的行情回填。** GitHub CI 只使用合成测试夹具，证明计算和 UI 行为，不代表 FRED、EIA 或世界银行真实网络源已端到端验收。
- 对冲突强度、商品贸易总体排名、战争成本等没有客观估计模型，不可将这项窗口研究当作这些指标。
- 尚未实现跨事件去重的统计推断、交易所日内时间级数据、ALFRED 当时可见的数据版本、bootstrap 显著性分析。未来需要完整研究设计与授权。

离线测试命令：

    python -m unittest discover -s tests -v
    node tests/test_study_frontend.cjs
    node tests/test_frontend.cjs
