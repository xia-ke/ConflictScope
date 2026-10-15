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
