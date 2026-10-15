# V3 UN Comtrade 真实贸易数据接入及授权核查

核查日期：**2026-10-15**。目标：在 ConflictScope 中准确展示目标国家/年份的特定 HS6 商品进口或出口世界伙伴国合计，而不是未经验证的「国家全部贸易」或错误地显示零。

## 一、当前状态：技术修复已提交，授权正式 API 仍待密钥联通验收

- **API 公共预览** `/public/v1/preview/C/A/H5`：按 UN Comtrade 官方定义，只能用于快速检查，最多 500 条记录，可能遗漏或出现空响应。不能据此证明报告国某类商品的完整贸易额、也不能将它视为合法的年度最终合计来源。
- **正式数据 API** `/data/v1/get/C/A/H5`：现在 `market_sources.comtrade()` 明确要求经批准的 `COMTRADE_API_KEY`，通过 HTTPS `Ocp-Apim-Subscription-Key` 头传递；不再在 API URL 中拼接密钥。该接口允许免费注册账户取得的适当 API Key，付费 Bulk API 并非完成本次单一 HS6 查询的前提。
- **2024 年乌克兰 HS2017 小麦（其他）**：`reporterCode=804&period=2024&partnerCode=0&cmdCode=100199&flowCode=X`，精确 H5，世界伙伴国合计、第二伙伴国和运输模式均为汇总值。单一记录的 `primaryValue` 与 `netWgt` 有独立来源意义；后者可能为空。不应将不同 HS 修订版 H5/H6/HS 的编码内容直接当作同一年度同一统计口径。
- **明确拒绝条件**：没有密钥、来源空/截断、不一致 `count`、非一个总计、报告国或流向不符、海关/运输/伙伴细分不符合、HS6 分类不符、数值负数或非有限值，全部失败且不向 SQLite 写入合成值。仅在真实有效记录的 `primaryValue=0` 时零值才合法。
- **运行范围**：当前代码只支持 `UKR/RUS/COD/YEM/SDN` 与原油/小麦/铜/黄金各一个**策划选定的 HS6 编码**。不能称为全球国家完整进出口商品排名，亦不能直接从四个 HS6 项目断言某国真正的主要进口/出口商品。

## 二、如何取得密钥及验证

1. 从 [UN Comtrade 官网](https://comtradeplus.un.org/) 登记账户，随后在 [My API Portal](https://comtradeplus.un.org/MyComtrade/APIPortal) 点击 **Generate API Key**，并查看 [Developer Portal](https://comtradedeveloper.un.org/) 上的调用文档和授权产品。免费注册账户通常有每天 500 次 Data API 调用额度；以账户当前权益为准。
2. 在本地终端设置 `COMTRADE_API_KEY` 环境变量；**不要提交到仓库、日志、配置 JSON、截图**，也不要将凭证粘贴到公开 Issue / PR。
3. 本地执行：

       python sync_market.py init
       python sync_market.py comtrade --country UKR --year 2024 --commodity wheat --flow export

4. 在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret 新增 `COMTRADE_API_KEY`。本分支的 [v3-comtrade-authorized.yml](../.github/workflows/v3-comtrade-authorized.yml) 会验证正式数据 API 的精确维度、期望单记录与 HTTP 成功。没有该密钥时工作流**明确失败**，而非自动跳到匿名预览。
5. 探针可选：`python scripts/live_source_probe.py --require-comtrade`。该命令只返回状态与记录数，不输出贸易额/密钥；即使探针通过，也不能以此视为拥有对外再发布许可。

## 三、UNSD 的来源许可 / 再分发政策核查

相关官方材料：

- [UN Comtrade API](https://uncomtrade.org/docs/un-comtrade-api/)
- [Subscription Plans](https://uncomtrade.org/docs/subscriptions/)
- [Policy on Comtrade Data Use](https://uncomtrade.org/docs/policy-on-comtrade-data-use/)
- [Re-dissemination of data](https://uncomtrade.org/docs/re-dissemination-of-data/)
- [UN COMTRADE LICENSE AGREEMENT](https://comtradeplus.un.org/LicenseAgreement)
- [What is data preview?](https://uncomtrade.org/docs/what-is-data-preview/)

官方政策不是「免费公开 API = 可任意公开复制」。UNSD 持有 Comtrade 数据版权，一般需遵守原有联合国许可条款。该政策同时列出了特定无需另外的分发许可费用/批准的豁免，应结合实际商业模式核实：

| 使用方式 | 当前判定 | 必要条件 |
|---|---|---|
| 项目开发者在本地运行、内部研究 | 条款中有内部使用类别 | 合法账户/配额、保留 UN Comtrade 归属、不得超出机构内部定义 |
| 免费公共数据可视化与分析、仅显示少量结果 | 官方重分发政策列有免额外分发许可的场景 | 免费提供、署名、审查实际记录总量及是否附大规模原始下载 |
| 免费向外提供原始记录下载，累计记录不超过 100,000 | 部分情形属于免额外分发许可类别 | 以整个产品/数据库总记录数计算，**不是单次查询数**；仍遵守许可协议 |
| 收费产品、商业数据服务、转卖/提供大量原始记录 | 需单独核查；可能需要分发许可或另行缴费 | 咨询 `subscriptions@un.org` 并与 UNSD 确认批准范围 |
| 将原始记录转换成 JSON、改列名或仅四舍五入 | **不构成实质性衍生数据** | 不能借「格式转换」绕过版权/再分发限制 |
| 综合其他来源创建新的增长率、份额、区域/行业统计指标 | UNSD 列为可能构成实质转换的例子 | 需实际形成新指标并保留引用；不意味着任何重组都自动获准 |

**政策文本在商业少量记录、盈利可视化与收费许可章节存在适用条件交叉**。在不知道 ConflictScope 最终是否公开、免费或盈利，以及预计开放数据量/下载方式前，不可能断言全面获得授权。涉及公开商业化运营时应向 [UNSD 联系邮箱](mailto:subscriptions@un.org) 提供产品说明，获得明确的书面许可确认后再发布。

建议数据署名：**Source: UN Comtrade (United Nations Statistics Division), reporter, year, HS2017 six-digit classification, flow and query date**。不应使用“UN 已确认该事件造成市场涨跌”的表达。

## 三点一、本地研究用途的默认授权判定

项目原始目标是 **127.0.0.1 本地运行的研究地图**，未指定商业收费或开放数据下载。若持续保持单用户内部分析、不开放远程访问、不向第三方重新传输 Comtrade 原始记录，属于 UNSD 公开政策明确列出的 **internal use** 场景，原则上不要求另外付费的数据再分发许可。该条件性结论不等于已经获取 API 访问权限、对外商业化许可或其他来源的再分发许可。

若扩展到公开免费网页，UNSD 对免费的数据可视化/分析应用、少量表图展示列有豁免，但仍须提供官方来源署名、避免无限制导出原始数据，并确认当前政策适用范围。若改为收费、商业化或大规模可提取数据服务，则重新核对再分发许可和订阅义务，向 UNSD 书面确认。

本地用途验收可暂视为**用途场景清晰（附条件）**；**Comtrade 正式 API 是否成功返回数据仍是硬性阻塞**。不要以“用途合法”推导“密钥已配置或贸易数据已验证”。

## 四、剩余发布阻塞与验收门禁

- [x] 匿名预览和有密钥的正式数据 API 区分
- [x] 正式取数请求维度完整性、记录数量、零值与错误数据测试
- [x] 密钥仅通过环境变量/HTTPS 请求头传输；未在 CI 日志输出贸易额
- [x] UNSD 官方 API、订阅额度与重分发政策来源记录
- [ ] GitHub Secret `COMTRADE_API_KEY` 提供授权密钥，正式 **UKR 2024 wheat H5 HS6** 数据 API 在 GitHub Actions 环境通过且 SQLite 导入复核
- [ ] 核实是否开放免费网页、是否提供原始数据库下载、是否收费、是否商业化、预计累计贸易数据记录数
- [ ] 对可能超出政策豁免的公开分发获得 UNSD 书面确认
- [ ] P2 的 11 条历史官方来源事件具名人工签核；EIA 直连可选测试；生产调度与浏览器验收

上述必需项未完成前，不应宣布完整 V3.0 生产发行就绪。当前所有 PR 保留 Draft，`main` 不变。

### 版本及统计安全

HS2022（H6）与 HS2017（H5）是不同商品分类版本；即使是相同六位数字，也应核实具体商品定义后才汇总。当前项目 **固定 HS2017 H5**，不在收到空数据时擅自换成 H6/HS 数据，也不把不同口径混入同一个序列。需要 H6 时，应独立扩充商品编码映射、数据库对应字段与测试。
