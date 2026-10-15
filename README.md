冲突观察 / Conflict Atlas
========================

一、本地运行

需要 Python 3.10 或以上，不需要 pip 安装依赖，也不需要 API Key。

Windows：解压后双击“启动-Windows.bat”。
macOS：在解压目录打开终端，运行 bash start.sh；也可双击 start.command。
Linux：在解压目录运行 bash start.sh。

通用命令：
  python server.py
  （macOS/Linux 通常使用 python3 server.py）

浏览器自动打开 http://127.0.0.1:8765 。
如没有自动打开，手动输入该网址。
停止服务：在终端按 Ctrl+C。
端口被占用：python server.py --port 8766
不要直接双击 static/index.html；需要本地 HTTP 服务提供新闻接口。

二、功能

• 世界地图按持续武装冲突、局部交火/武装暴力、区域外溢三类着色。
• 点击地图、关注地区列表或搜索结果，在右侧显示地区情况和新闻链接。
• 支持国家中英文搜索、分类筛选、滚轮缩放、拖动平移、地图重置和“一键聚焦地区”。
• 当前选择会写入本地页面 URL 参数，刷新页面后可恢复到同一地区与分类。
• 新闻可筛选最近 7/30/90 天，以及联合国新闻/媒体报道；右侧显示 Bing / BBC / UN News 各源响应状态。
• 新闻源为 Bing News、BBC World、UN News，新闻保持来源原文。
• 新闻成功结果缓存 15 分钟，点击“刷新”会主动重新获取在线订阅源。
• 断网或未取得近期结果时显示缓存/已核实快照，清楚注明状态。
• 地图数据和代码全部随项目提供，地图本身不依赖在线 CDN。

三、数据范围与更新

首版包含 19 个国家或地区的部分观察清单，地图快照截至 2026-10-13。
这不是全球冲突的完整数据库，未着色不代表没有冲突。
每个地区均附有来源链接和依据日期；不同来源的日期可能早于快照日期。
国家着色表示关联类型，绝不表示该国全部领土是战区。
海地的武装帮派暴力与国家间战争含义不同，右侧说明已区分。
区域外溢包括难民接收、边境安全和海上通道影响，并不表示当地发生地面战争。
没有实时前线、军队位置、控制线或伤亡强度评分。

地图分类目前人工维护，不会根据新闻标题自动改变。
更新地图：编辑 data/conflicts.json 中相应国家的 category、areas、summary、sources，
同时修改顶层 updated 日期，然后重启服务。
分类可取：persistent / localized / spillover。
增加国家：参照已有条目添加国家 ISO alpha-3 编码和其他字段。
添加新闻快照：编辑 data/seed-news.json；新闻必须附实际来源、URL 和发布时间。
新闻通常按需获取，页面未打开时不会在后台轮询。
最新状态变化应由人工结合来源核实；新闻聚合可能有遗漏或检索误匹配。

四、联网与故障

运行电脑需要能访问 www.bing.com、feeds.bbci.co.uk、news.un.org 才能更新新闻。
本地服务默认只监听 127.0.0.1，访问仅限本机。
使用公司代理时可按系统环境配置 HTTPS_PROXY，Python 将使用常规代理设置。
新闻列表为空时可切换时间范围、来源筛选，或使用提供的外部检索入口。
在线来源和链接可能调整；打不开某条报道时可尝试其他来源。
接口错误时，确认启动终端还在运行。
首次获取通常需要数秒，某个来源不可用时会有明确提示。

五、文件

server.py                 本地网页与 RSS 聚合服务（Python 标准库）
static/index.html          页面结构
static/style.css           深色地图看板样式
static/app.js              地图、交互、国家选择与新闻面板
static/world.json          离线世界地图（简化 Natural Earth 数据）
data/conflicts.json        人工维护的状态与核实来源
data/seed-news.json        已核实的新闻快照
tests/test_server.py       新闻回退、排序、安全和 HTTP 接口检查
.cache/                    运行时产生的新闻缓存，删除后可重新获取

验证：python -m unittest discover -s tests -v
如已安装 Node.js，可运行 node tests/test_frontend.cjs 验证前端逻辑。
Node.js 不属于运行网页的必需依赖。

六、地图数据许可

地图数据来自 Natural Earth，公共领域：
https://www.naturalearthdata.com/about/terms-of-use/
原始分发：
https://github.com/datasets/geo-countries
https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson
已进行几何简化并保留国别属性，SVG 采用 Natural Earth I 投影。
地图边界为地理概览，不构成对争议边界的裁定。

本地服务接口、新闻回退逻辑与前端核心交互均有自动化验证。


## V2.1 · 多信息源采集

原有 RSS（Bing News、BBC News、UN News）继续工作，新增 **GDELT DOC 2.0** 新闻索引采集，以及可选 **ReliefWeb** 人道主义报告索引。结果仍通过现有 `/api/news?country=UKR&days=7` 接口返回，无需改变地图前端。

直接运行采集器（Python 3.10+，不需要第三方包）：

```bash
python source_collectors.py --country UKR --days 7
python source_collectors.py --country SDN --days 30 --output sudan.json
python server.py
```

ReliefWeb 需要事先取得官方批准的 API `appname`，再设置环境变量 `RELIEFWEB_APPNAME`；未配置时自动跳过 ReliefWeb。设置 `CONFLICTSCOPE_STRUCTURED_SOURCES=0` 可禁用结构化来源，仅保留原有 RSS。采集器使用超时、大小限制、URL 检查、日期统一化及来源失败隔离；无网络时仍可回退到已有缓存或快照。

**数据解释**：GDELT 是新闻索引，ReliefWeb 是报告索引；被收录不等于独立核实事件，也不应直接据此调整冲突级别。自动抓取受各来源服务条款、可用性和限流约束。

执行测试：

```bash
python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```


## V3.0 P0 · 地缘经济数据底座（开发分支）

这一阶段只落地本地数据库规范、可追溯模型和离线回归，不代表已接入商品行情或具备因果分析能力。现有地图、冲突分类及 `/api/news` 保持兼容。

```bash
python geoeconomics.py init
python geoeconomics.py status
python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

默认数据库是 `data/geoeconomics.sqlite3`（被 Git 忽略）。可以使用 `--db /path/to/file.sqlite3` 自定义路径；初始化可重复执行，默认从当前 `conflicts.json` 补齐观察国家，但不会编造任何贸易、价格或冲突事件记录。

P0 表结构包括：`countries`、`data_sources`（来源与再分发状态）、`commodities`、`commodity_hs_codes`（HS 修订版本）、`trade_flows`（贸易方向、期间、镜像估计标志）、`price_series`、`price_observations`（行情基准/币种/单位/观察日）、`events`（发生/宣布/生效/发布时间分别记录）、`event_links`（事件关联国家/商品/基础设施与证据）。所有历史表在 P0 初始化后保持为空；后续阶段只能通过经过验证的适配器填充，且不得从新闻标题自动推断价格因果关系。

新增源级字段：来源网址、访问权限、许可说明、再分发标志；源内容尚未自动导入。未来开发阶段：P1 接入贸易与价格源；P2 事件核实/时间轴；P3 事件窗口分析与置信度；P4 定期同步和数据质量监控。建议采用独立 PR 验收，再合并进 `main`。


## V3.0 P1 · 真实贸易和大宗商品价格（开发分支）

P1 采用**显式命令行同步 → SQLite 事务写入 → 只读 HTTP 查询**。地图新闻接口不自动抓市场数据，避免触发第三方 API 限流；未导入任何记录时不能展示虚构的价格或贸易量。

**官方参考来源**
- UN Comtrade（HTTPS API）：https://comtradeapi.un.org/ ，预览接口无需密钥但有记录与频率限制，不能做全量排名；正式密钥可通过 `COMTRADE_API_KEY` 环境变量提供。
- World Bank Pink Sheet（官方可下载 XLSX）：https://www.worldbank.org/en/research/commodity-markets ，从该页的 Monthly prices 下载最新月度工作簿。工作簿导入无需 pandas/openpyxl，仍为 Python 标准库。
- EIA API v2（Brent / WTI 日度美元/桶现货）：https://www.eia.gov/opendata/ ，申请免费的 `EIA_API_KEY`。请勿将密钥提交到 GitHub。

```bash
# 初始化商品、HS2017 六位编码和来源元数据
python sync_market.py init
python sync_market.py catalog

# 单个 HS6 商品对世界贸易额与净重。示例为乌克兰小麦（HS 100199），年度出口。
python sync_market.py comtrade --country UKR --year 2024 --commodity wheat --flow export

# 世界银行官网下载 CMO-Historical-Data-Monthly.xlsx 后：
python sync_market.py pink-sheet --file CMO-Historical-Data-Monthly.xlsx
# 从列名清单中选中官方数据真实列头，例如 Copper，并核对对应价格单位：
python sync_market.py pink-sheet --file CMO-Historical-Data-Monthly.xlsx \
  --column Copper --commodity copper --unit 'metric ton' --series-id wb:copper

# EIA key 必须先配置在系统环境变量 EIA_API_KEY 中
python sync_market.py eia --series RBRTE --start 2026-09-01 --end 2026-10-01
python sync_market.py eia --series RWTC --start 2026-09-01 --end 2026-10-01

# 启动现有地图（数据接口是只读的）
python server.py
```

**新增 API**：`GET /api/market/status`、`GET /api/commodities`、`GET /api/trade/UKR?year=2024&flow=export`、`GET /api/prices`、`GET /api/prices/wb:copper?start=2024-01-01&end=2026-10-01`。未初始化市场 DB 时返回 503；不存在的价格序列返回 404。

**数据语义限制（必须在 UI 中遵守）**：
- Comtrade 当前只同步 5 个试点国家的明确指定商品（油、小麦、铜、金；各对应一个 HS2017 六位编码）、`partnerCode=0` 的世界合计。返回 `scope=selected_hs6_world_partner_only`，**不是所有出口、主要出口排名，也不是产量份额**。二次伙伴、运输模式或报关子维度不符合要求时拒绝入库；预览截断也拒绝导入。
- Pink Sheet 月度价格与 EIA 日度油价必须通过不同 `series_id`、原始基准、币种、单位区分。导入时用户明确核对工作簿列名和单位，代码不猜测它们的映射。
- 空数据表示未导入/无结果，不等于零；查询元数据提供来源、抓取时间与频率。制裁与冲突事件关联将由 P2 审核后接入，P1 不进行因果推断。
- 来源转载须遵守各机构使用条款，数据库的 `redistribution_allowed` 默认为未知（NULL），不得当作授权许可。

**测试**：`python -m unittest discover -s tests -v`，`node tests/test_frontend.cjs`。CI 在 Python 3.10 和 3.12 上运行。此次先交付数据库与接口，不把尚无数据的市场模块强行塞入现有右侧新闻面板。


## V3.0 P2 · 审核事件和多轨道时间轴（开发分支）

P2 在现有地图和 P1 商品面板旁增加“重大事件时间轴”，按冲突/停火、制裁/贸易政策、供应、物流四类轨道展示。支持发生/公告/生效/来源发表日期基准切换。未审核的记录、普通新闻标题及测试数据不会自动进入时间轴。

执行：

    python sync_market.py init
    python sync_events.py validate --file reviewed-events.json
    python sync_events.py import --file reviewed-events.json
    python sync_events.py list --country UKR
    python server.py

数据库自动从 v1 非破坏性迁移到 v2；新增 event_evidence（多证据）和 event_reviews（编辑审核）表。完全相同的事件 ID/内容允许幂等重导；同 ID 内容变更会拒绝，不做静默覆盖。新建数据库的事件记录仍为零。

接口：GET /api/timeline?country=UKR&basis=impact 和 GET /api/events?country=UKR&commodity=wheat&type=embargo&start=2024-01-01&end=2026-10-15。查询不触发远程抓取。

完整导入字段契约、编辑审核与证据要求：参见 [docs/event-import.md](docs/event-import.md)。此阶段尚未自动导入 UCDP/ACLED 历史事件，未执行价格因果分析，不自动计算冲突烈度。

## P2 第一批官方来源历史事件（2022—2024）

已纳入 11 条由 AI 辅助对照官方原始文件整理的研究事件，数据在 `data/official-events-2022-2024.json`。涉及 2022 年俄罗斯入侵、欧盟对俄能源措施、联合国黑海粮食倡议、红海航运中断报告及美国针对俄罗斯金属的限制。

**审核等级：官方来源已核对（AI 辅助），尚待人工编辑复核。** `reviewer` 字段显式记录了非人工核对身份；时间轴的 `research_ai_assisted=true`、`editor_reviewed=false`；切勿把该批数据宣传为独立人工审计完成。

项目不会从新闻自动生成历史事件。为避免无意中改变用户本地数据库，下载分支后需手动运行以下命令将历史记录导入本地数据库：

    python sync_market.py init
    python sync_events.py validate --file data/official-events-2022-2024.json
    python sync_events.py import --file data/official-events-2022-2024.json
    python sync_events.py list --country UKR
    python sync_events.py list --country RUS
    python sync_events.py list --country YEM
    python server.py

重复导入相同数据不增加记录；相同 ID 但内容不同的记录拒绝写入。以上事件从 2022—2024 年官方文件归档中构建，不代表截至今天的最新冲突态势，也不能证明事件造成具体价格变动。**数据源清单和时间基准解释**见 `docs/official-events-2022-2024.md`。
