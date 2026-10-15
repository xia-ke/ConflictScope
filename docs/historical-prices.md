# 2022—2024 年冲突事件研究：大宗商品历史价格回填

## 目标、来源与开源借鉴

为 P2 的历史冲突、黑海粮食运输、对俄制裁、红海航运事件建立可追溯的商品价格序列，供之后进行事件研究。**时间相关不等于因果关系。**

脚本 `historical_prices.py` 面向 Python 3.10+，沿用现有 SQLite 结构；不需要 pandas、OpenBB 或额外行情数据库。

架构参考：
- [OpenBB](https://github.com/OpenBB-finance/OpenBB)：每个数据提供商独立的解析与数据契约，查询/验证/存储分离。只借鉴流程，不复制框架代码。
- [fredapi](https://github.com/mortada/fredapi)：FRED 和 ALFRED 的价格观察日期/后期修订区分。P3 需要历史实时版本时再引入 ALFRED，不把当前数值假装成当时首次公布值。
- [pandas-datareader](https://github.com/pydata/pandas-datareader)：FRED CSV 历史序列读取；本脚本仍使用 Python 标准库。
- [datasets/commodity-prices](https://github.com/datasets/commodity-prices)：原始文件下载、单位/日期清洗、来源元数据保存。其旧归档不保证当前有效，部分 IMF 数据含版权限制。

## 价格源与频率

| Source ID | 价格品种 | 频率 | 币种/单位 | 上游来源 |
| --- | --- | --- | --- | --- |
| fred:DCOILBRENTEU | Brent Europe 现货 | 日度 | USD/桶 | EIA 经 FRED |
| fred:DCOILWTICO | WTI Cushing 现货 | 日度 | USD/桶 | EIA 经 FRED |
| fred:PWHEAMTUSDM | 全球小麦均价 | 月度 | USD/公吨 | IMF 经 FRED，带版权条件 |
| fred:PCOPPUSDM | 全球铜均价 | 月度 | USD/公吨 | IMF 经 FRED，带版权条件 |
| wb:copper 等 | 世界银行原始列名 | 月度 | 以官方表列单位为准 | World Bank Pink Sheet |
| eia:RBRTE / eia:RWTC | Brent/WTI 现货 | 日度 | USD/桶 | EIA API（需要密钥） |

重要：FRED 的 IMF 月度数据可能具有版权限制，**不能因为 CSV 可下载就公开转售或复制全部序列**。先用于授权的本地研究；公开网站应只在获准的范围呈现，并保留正式来源 URL 及归属信息。EIA/FRED 两种方式获得的 Brent/WTI 有同一上游数据但不同取数渠道，用不同的 `series_id` 保存，不应当作两个独立市场。

## 运行示例

在分支 `feature/v3-historical-price-backfill`：

    python sync_market.py init
    python sync_events.py import --file data/official-events-2022-2024.json

一、无密钥，日度原油，指定历史区间（首次建议围绕 P2 的 2022—2024 年事件，向前后留有观察窗口）：

    python historical_prices.py fred --series DCOILBRENTEU --start 2021-01-01 --end 2025-12-31
    python historical_prices.py fred --series DCOILWTICO --start 2021-01-01 --end 2025-12-31

二、小麦、铜：IMF 月度平均值（不能用来分析冲突发生 1 天后的变化）：

    python historical_prices.py fred --series PWHEAMTUSDM --start 2021-01-01 --end 2025-12-31
    python historical_prices.py fred --series PCOPPUSDM --start 2021-01-01 --end 2025-12-31

三、世界银行月度历史工作簿：

    # 可直接尝试官方历史工作簿地址（固定发布链接，若变动应改从官网重新下载）
    python historical_prices.py worldbank --download --list-columns
    # 明确确认 Excel 中 Copper 列是 USD/metric ton 再导入
    python historical_prices.py worldbank --download --column Copper --commodity copper --unit "metric ton" --series-id wb:copper --start 2021-01-01 --end 2025-12-31

也可以在 [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets) 下载最新 `CMO-Historical-Data-Monthly.xlsx`，避免使用早期固定链接：

    python historical_prices.py worldbank --file CMO-Historical-Data-Monthly.xlsx --list-columns
    python historical_prices.py worldbank --file CMO-Historical-Data-Monthly.xlsx --column Copper --commodity copper --unit "metric ton" --series-id wb:copper --start 2021-01-01 --end 2025-12-31

Gold、Wheat 等列要根据工作簿的实际标题、基准和官方单位自行选取，**不可猜测与 HS6 贸易商品完全等价**。当相同 series ID 既有不同基准或单位时，SQLite 拒绝覆盖，要求使用新 series ID。

四、若 FRED CSV 因地区网络、限流或源站变动无法访问，可使用 EIA 官方 API 获取原油现货，事先在系统环境变量设置 `EIA_API_KEY`：

    python historical_prices.py eia --series RBRTE --start 2021-01-01 --end 2025-12-31
    python historical_prices.py eia --series RWTC --start 2021-01-01 --end 2025-12-31

注意 EIA 数据源约束：既有 P1 `market_sources.eia_spot` 对单次请求最多接受 5000 条，超过即报错并提示缩小日期区间，不会默默截断。

## 回填验证

    python historical_prices.py coverage --country UKR
    python historical_prices.py coverage --country RUS
    python historical_prices.py fred --series DCOILBRENTEU --start 2021-01-01 --end 2025-12-31 --dry-run

`coverage` 从本地 SQLite 读取已经存入的序列数量、最早/最晚观测日期、频率与来源，并列出该国家已录入事件的时间跨度。它不是事件研究统计结果。

现有 REST API 不需更改：

- `GET /api/prices`：列表与来源信息
- `GET /api/prices/fred:DCOILBRENTEU?start=2022-01-01&end=2023-12-31`：日度价格
- `GET /api/prices/fred:PWHEAMTUSDM?start=2022-01-01&end=2023-12-31`：月度价格

不要把月均价前向填充成日度市场交易价格。研究中应当按交易日与事件公告时区对齐；历史原始文件没有实际“首次发布时点”的地方，也不能擅自填写 `source_published_at`。回填当前修订值有可能带事后修订偏差，正式 P3 事件研究应考虑数据版本历史。

## 数据安全与许可

- 不将 `EIA_API_KEY`、FRED API key 等写入代码、日志或 Git。
- 本地生成的 `data/geoeconomics.sqlite3` 已在 `.gitignore` 排除，**不要提交第三方行情数据快照**。
- 源站缺失数据采用缺失，不填零；数据重复按 `(series_id,observed_at)` 幂等更新。价格小于等于 0 会被当前 P1 校验拒绝；若未来研究 2020 年 WTI 负油价，必须先扩充现有定价模型和测试。
- `--dry-run` 仅执行远端下载和格式校验，不修改 SQLite；请求失败不会产生合成行情。
- 不对历史价格或冲突因果关系自动得出结论，也不把不同质量、不同计价单位的报价强行并线。

CI 运行 Python 3.10、3.12 及离线 FRED CSV / 世界银行工作簿测试；**官方实时网络连通性仍需要在用户有访问权限的环境验证**。
