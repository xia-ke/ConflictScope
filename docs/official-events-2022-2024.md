# ConflictScope 官方文献历史事件首批研究清单（2022—2024）

这是面向 P2 时间轴的**AI 辅助官方来源核对**，研究核对日期：2026-10-15。**尚无独立人工编辑签核**；不代表法院裁判、实时危机警报或价格因果结论。数据文件为 `data/official-events-2022-2024.json`，原始官方 URL、证据说明、四种日期与国家/商品关联以该 JSON 为准。

| 编号 | 时间轴主要日期及类型 | 事件 | 主要原始来源 |
|---|---|---|---|
| 01 | 2022-02-24 · 发生 | 俄罗斯军事行动扩大至乌克兰 | [联合国秘书长声明](https://press.un.org/en/2022/sgsm21158.doc.htm) |
| 02 | 2022-06-03 · 公告 | 欧盟第六轮对俄石油进口制裁，含过渡期及例外 | [欧盟理事会](https://www.consilium.europa.eu/en/press/press-releases/2022/06/03/russia-s-aggression-against-ukraine-eu-adopts-sixth-package-of-sanctions/) |
| 03 | 2022-07-22 · 签署公告 | 黑海粮食倡议签署 | [联合国发言人记录](https://www.un.org/sg/en/content/highlight/2022-07-22.html) |
| 04 | 2022-08-01 · 发生 | 首艘 Razoni 号按倡议从敖德萨离港，运载**玉米而非小麦** | [联合国在土耳其机构](https://turkiye.un.org/en/192939-statement-attributable-spokesperson-secretary-general-first-commercial-vessel-sailing-part) |
| 05 | 2022-12-03 公告 / **12-05 生效** | 对俄原油海运相关服务 60 美元/桶价格上限 | [欧盟理事会](https://www.consilium.europa.eu/en/press/press-releases/2022/12/03/russian-oil-eu-agrees-on-level-of-price-cap/) |
| 06 | 2023-02-04 公告 / **02-05 生效** | 对俄石油产品 45 / 100 美元/桶双档上限 | [欧盟理事会](https://www.consilium.europa.eu/en/press/press-releases/2023/02/04/eu-agrees-on-level-of-price-caps-for-russian-petroleum-products/) |
| 07 | 2023-07-17 · 决定/终止 | 俄罗斯结束参与黑海粮食倡议 | [联合国秘书长](https://www.un.org/sg/en/content/highlight/2023-07-17.html) |
| 08 | 2024-01-03 · **IMO 报告日** | 记录自 2023 年 11 月以来红海商船遭袭 | [国际海事组织 IMO](https://www.imo.org/en/mediacentre/secretarygeneral/pages/maintenance-of-international-peace-and-security-%E2%80%93-united-nations-security-council%2C-9525th-meeting.aspx) |
| 09 | 2024-01-26 · **UNCTAD 报告日** | 红海危机下苏伊士通航量下滑，报告反映持续影响 | [UNCTAD](https://unctad.org/press-material/unctad-raises-alarms-escalating-disruptions-global-trade-due-geopolitical-tensions) |
| 10 | 2024-04-12 · 公告及措施日期 | 美国对俄铝铜镍进口及特定服务限制，2024-04-13 前生产金属有例外 | [OFAC FAQ 1168](https://ofac.treasury.gov/faqs/1168) |
| 11 | 2024-08-28 · **IMO 报告日** | Sounion 原油运输船在红海受袭后 IMO 发表声明；确切攻击日未由该声明确认 | [国际海事组织 IMO](https://www.imo.org/en/MediaCentre/SecretaryGeneral/Pages/Statement-MV-SOUNION.aspx) |

## 时间与范围的特定注意事项

- `occurred_at` 仅记录来源能支持的实际发生日；没有确切攻击日时留空。对红海总体风险报告只写 `published_at`，不要将报道日期误当攻击首日。
- `announced_at` 与 `effective_at` 分离。2022-06-03 欧盟制裁包含阶段性生效及国别例外，因此不人为填写统一的原油进口禁令生效日。
- 成品油 CN2710 不能直接对应 HS270900 原油基准，2023-02-05 事件只链接到俄罗斯及成品油航运服务，不虚构商品映射。
- 2024 年美国金属措施有适用范围及生产日期例外；铜关联为金属大类，不代表 HS740311 全部进出口直接归零。
- 黑海粮食倡议是涉及小麦在内的粮食运输安排，但 2022-08-01 Razoni 首船实际运载玉米，故该记录没有标为小麦专船。
- 红海事件归到 `YEM` 仅表示**区域相关的也门/红海航运风险**，不代表事件发生在也门陆地、也不证明某国港口停运。
- 所有 `impact_mechanism` 是需要进一步研究的传播渠道，不代表已证实的价格因果效应。

## 如何启用

仓库中提交的是结构化 JSON 证据批次，不是把 SQLite 二进制数据库提交到 Git。选择 P2 分支后执行：

    python sync_market.py init
    python sync_events.py validate --file data/official-events-2022-2024.json
    python sync_events.py import --file data/official-events-2022-2024.json
    python sync_events.py list --country UKR
    python server.py

可在右侧时间轴看到这批真实历史研究事件。前端明确标注「AI 辅助，尚未人工复核」，不自动发布“官方机构已经认定商品价格上涨由该事件造成”等错误结论。

建议下一步安排人工复审，核查原始法律适用范围、每条中文概述、事件修订和时区细节；之后才能提升审计等级。
