# ConflictScope P4 — 定时同步与数据质量运维

P4 把联网采集与网页 API 解耦。网页仅只读查询 SQLite；**定时同步需要在运行服务器的机器上配置系统任务**，GitHub PR 与 CI 不会自动替你的本地数据库定时更新。

## 1. 同步计划

默认配置：`data/sync-plan.example.json`。保守启用两条 FRED/EIA 来源的日度现货价格序列（Brent/WTI）以及一个**官方事件 JSON 文件变化检查**。不会自动将新闻、官方来源文件变更当成已审核事件。不会默认拉取有 IMF 版权条件的 FRED 月度商品序列。

```bash
python sync_tasks.py validate
python sync_tasks.py once
python sync_tasks.py status
```

也支持路径自定义：

```bash
python sync_tasks.py --db /absolute/path/geoeconomics.sqlite3 --plan /absolute/path/sync-plan.json once
```

计划只允许四类 provider，不允许执行任意命令：

| provider | 必要字段 | 行为 |
|---|---|---|
| `fred` | `series`, `days_back` | 仅指定白名单序列，经 FRED CSV 下载并做严格字段和日期验证 |
| `eia` | `series`, `days_back` | 通过 `EIA_API_KEY` 访问 EIA 日度 API，密钥在环境变量 |
| `worldbank` | `column`, `commodity`, `unit`, `series_id`, `days_back` | 官方 Pink Sheet 月度表，列名和单位须人工确认；发布下载地址变化会失败并告警 |
| `event-watch` | `file` | 只读核查 `data/` 内 JSON 的格式与 SHA-256；发生变化时要求人工审核，**不会自动导入** |

所有任务都需要唯一 `id`。联网任务的 `days_back` 范围是 2–365 天。请为计划添加或复制新任务时选择真实支持的商品编码，不要假设所有国家都有完整的商品数据。

## 2. 系统调度

### Linux/macOS cron：每天 09:00

先确保有 `python3`，在本机执行 `crontab -e` 添加（替换成实际路径）：

```cron
0 9 * * * cd /absolute/path/ConflictScope && /usr/bin/python3 sync_tasks.py once >> /absolute/path/conflictscope-sync.log 2>&1
```

cron 使用**执行机本地时区**，不是用户浏览器时区。使用虚拟环境时换成其 Python 可执行路径。日志可能包含来源状态与任务 ID，不应对外公开；程序不会主动写出 API Key。推荐配置日志轮转或由服务管理器管理日志。

### Windows 任务计划程序

创建“基本任务”→每日→09:00→启动程序，填写：

- 程序：安装的 `python.exe` 的完整绝对路径；
- 参数：`sync_tasks.py once`；
- 起始于：ConflictScope 工程的完整绝对目录。

在“设置”中选择**若任务已运行则不启动新实例**。使用 EIA 时在任务运行用户的环境里配置密钥，而不是写入 .json。程序本身还使用单独的 SQLite 任务租约防止并行写入。

### 运行机制

一个 `once` 批次依次执行 1–20 个任务。失效的单个任务会记录 `error`，**保留上次成功数据库记录**，然后执行其余任务。仅对连接超时、429 与 5xx 等暂时性网络错误进行最多 2 次重试；格式错误、无数据和未授权配置不会重复请求。批次有失败时命令退出码非零，可供 cron 邮件或外部监控系统使用；**没有内置邮件、短信或主动推送告警**。

同步进程获取一个最长约 3 小时的 SQLite 租约，避免两个调度器同时运行同一数据库。如果异常结束，租约到期后下次任务可再竞争；请先确认旧任务真的已停止，避免长任务和新任务并行。它并非分布式高可用调度器。

## 3. 质量检查与版本审计

```bash
python sync_tasks.py status
# 本地 HTTP 服务器启动后
curl http://127.0.0.1:8765/api/sync/status
```

健康报告显示：

- 每个任务的最近检查时间、上次成功时间、最近有效行情**观测日期**、当前状态及失败次数；
- 任务运行流水（最多最近 40 次，含次数、完成时间、成功/失败类型和入库数量）；
- 商品价格序列的第一笔/最新有效日期、历史价格修订次数；
- 日度超过 10 自然日、月度超过 90 自然日未见新报价的“观测日陈旧”提醒；旧历史专项数据即便正确，也可能触发这类提示；
- 来源检查有变化时的 `source_change_requires_review` 待人工核查状态。

采集日期、统计观察日期与来源发布日期是不同概念，不能将“刚成功同步”理解为“今天有新价格”。`price_revisions` 仅在已有同一序列/观察日数值发生变化时新增审计记录；重复获取完全相同数据不会重复记账。审计行记录旧值、新值和检测时间，不记录行情源的全部原始受限数据包。

源内容指纹对于常规 FRED/EIA/World Bank 表示**本次回填窗口内的规范化样本**，因为滚动窗口移动，它未必是上游发布版本的固定标识；对于事件文件则为文件字节 SHA-256。

## 4. 官方事件来源发生变化后

`event-watch` 检查的数据包可能已经通过 P2 的技术准入程序，但它的内容更新仍需要逐条对照正式机构原文、法律生效范围和发布/发生日期。同步任务只会把 `review_pending` 设为 1，**不调用 `sync_events.py import`**。

人工完成来源变更比对后，可单独确认“已处理这次文件变化提醒”（不等于事件通过编辑审核）：

```bash
python sync_tasks.py ack-change --job official-events-watch
```

真正的事件内容更改依旧需要独立遵循 [事件准入规范](event-import.md) 并通过编辑审核；相同 ID 内容不一致仍会被 P2 拒绝，不能绕过不可变审计链。

## 5. 安全、公开部署与限制

- 默认网页服务仅绑定 127.0.0.1。公开部署时要在代理层做认证和授权，并检查是否能再分发受版权保护的 IMF/FRED 行情。新的状态 API 不包含密钥、完整错误消息或原始响应正文。
- `data/geoeconomics.sqlite3` 由 .gitignore 排除，**不能提交私人数据库或外部商业价格数据库**。
- FRED 的公共 CSV 连接可受地域、代理、速率限制影响；CI 只用离线 fixture，不代表上游实时可用。
- 若想向用户推送来源变动，需另外接入邮件/通知渠道；当前是可供定时任务读取的退出码、JSON 状态和网页提示。
- P4 没有自动批准冲突事件、没有自动更改地图国家着色、没有进行新的价格因果推断。
- 未来可进一步建设完整商业行情授权、增量更新游标、来源版本快照、监控指标导出和运维通知。

## 6. 验证

```bash
python -m unittest discover -s tests -v
node tests/test_sync_status_frontend.cjs
python sync_tasks.py validate
python sync_tasks.py status
```

初始化前 `status` 会报错而不是隐式造一个空库：执行 `python sync_market.py init` 或首次 `once` 后再查。

