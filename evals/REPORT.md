# 评测体系建设报告：从「跑得通」到「可信」

本报告记录这套 LLM 评测体系从「几条自编数据跑通」到「较大数据集 + 持续优化」的
完整过程：每个阶段跑了什么命令、拿到什么数据、发现什么问题、怎么改、改完效果
如何。

---

## 0. 结论速览

| | 阶段一（小样本跑通） | 阶段二（大数据集 + 优化） |
|---|---|---|
| 数据 | 3 条残留快照 vs 25/16 条数据集 | 25 条匹配题、16 道面试题×4 档、26 条查询 / 186 段语料 |
| 单元测试 | 59 passed | 135 passed |
| 评测指标 | **无效**（快照只覆盖 3 条，其余无预测值） | 三套基线齐全，离线可复现，CI 真跑 |
| CI 的 evals job | 空转（无快照、无基线，跳过即绿） | 离线重算 + 逐指标判定，三套件 exit=0 |
| 主要发现 | 分数是假的 | 4 类真实缺陷（见下） |

阶段二一共发现并修掉 4 类问题：

1. **模型多说话** → 整条样本作废（解析失败）
2. **模型少给字段** → 整条样本作废（校验失败）
3. **同样的容错只写在面试模块**，匹配模块裸奔
4. **回归判定本身是错的** → 真退化被放过、结构变化被误判、噪声被当成回归

---

## 1. 环境与常用命令

必须在项目 conda 环境里跑（base 环境缺 `dotenv`、SQLAlchemy 版本也不对），
且该环境**没装 pytest**，所以用 CI 同款命令：

```bash
conda activate ai-job-assistant

# 单元测试（CI 也是这条；-t . 让 tests/__init__.py 生效，屏蔽测试里的日志噪声）
python -m unittest discover -s tests -t .

# 离线评测：重算快照 + 与基线对比（免费，CI 跑这个）
python -m evals.run_all --compare

# 单个套件的判定（--check 让 gated 回归返回非零）
python -m evals.compare --suite job_match --check

# 真实调用 provider（花钱）
python -m evals.run_job_match --live
python -m evals.run_interview_scoring --live --repeats 3
python -m evals.run_rag_retrieval --live

# 把一次满意的运行设为基线
python -m evals.compare --suite <suite> --promote

# provider 稳定性历史
python -m evals.provider_stats --live-only
```

live 会真实产生费用：job_match 25 次对话；interview 16 题 × 4 档 × 3 次重复
（≈96 次调用）；rag 需要 embed 186 段语料 + 26 条查询。

---

## 2. 阶段一：几条自编数据跑通

### 2.1 做了什么

数据集就位后先跑单元测试，再按 CI 的方式跑一遍评测。

```bash
python -m unittest discover -s tests -t .   # 59 passed
python -m evals.run_all --compare           # 三套件 "No snapshot found"
```

`run_all` 直接跳过，因为顶层没有 `*.latest.jsonl` 快照（只剩 `snapshots/archive/`）。
用归档快照强制重算：

```bash
python -m evals.run_job_match --snapshot evals/snapshots/archive/job_match.deepseek-chat.latest.jsonl
python -m evals.run_interview_scoring --snapshot evals/snapshots/archive/interview_scoring.deepseek-chat.latest.jsonl
```

### 2.2 得到的数据

| 套件 | 数据集样本 | 归档快照预测 | 指标 |
|---|---|---|---|
| job_match | 25 | **3**（jm-01..03） | 匹配分误差 **34.3**、容差命中率 **0.0**、技能召回 0.31 |
| interview_scoring | 16 | **3**（is-01..03） | 仅覆盖 3 条 |
| rag_retrieval | 26 查询 | 无 | 无法评分 |

### 2.3 结论与处理

- **单元测试全绿，但评测数字毫无意义**：那几个数字不是模型变差，是 22/13 条新样本
  根本没有预测值，拿 3 条旧答案硬凑出来的。
- 诊断过程生成的 4 个误导性报告已删除，目录恢复原状。
- **CI 的 evals job 当时是空转**：既没快照也没基线，`compare --check` 拿不到基线就返回 0，
  绿灯是假的。

这一阶段的收获是方法论上的：**覆盖率必须自己成为指标**。如果只看「误差多少分」，
22 条没答案的样本不会告诉你任何异常。

---

## 3. 阶段二：较大数据集测试与持续优化

### 3.1 建立三套真实基线（让 CI 真跑）

**做法**：三套件各跑一次 live，然后 `--promote`。

```bash
python -m evals.run_rag_retrieval --live --notes "baseline: 186 chunks / 26 queries"
python -m evals.compare --suite rag_retrieval --promote
python -m evals.run_job_match --live
python -m evals.run_interview_scoring --live --repeats 3
python -m evals.compare --suite job_match --promote
python -m evals.compare --suite interview_scoring --promote
```

**基线数据**（提交 `667d687`，82 个文件）：

| 套件 | 基线指标 |
|---|---|
| job_match（25 条） | 误差 12.56 · 容差命中 0.76 · 已匹配/缺失技能召回 0.987/1.0 · 合格率 1.0 |
| rag_retrieval（26 查询 / 186 段） | recall@1 0.397 · recall@3 0.737 · recall@5 0.846 · mrr 0.667 |
| interview_scoring（16 题） | 区分度 1.0 · 好坏分差 70.8 · 档位顺序正确率 1.0 · 好/中/偏题/差 = 88/55/20/15 · 合格率 **0.9375** |

**效果**：离线重算与 live 结果完全一致（delta 全 0），说明快照自洽、CI 能免费复现。
**发现**：面试套件有 1 条样本失败（合格率 0.9375），引出下一阶段。

---

### 3.2 优化一：模型「话太多」导致整条作废（is-12）

**现象**：`is-12` 报 `JSONDecodeError: Extra data: line 1 column 769`。
要求模型只回 JSON，它回完 JSON 又补了几句客套话，程序直接整条丢弃——而那段 JSON 本身是好的。

**改法**：新增 `extract_json_object()`——先整体 `json.loads`，失败则剥掉 ```json 围栏，
再用 `JSONDecoder().raw_decode` 从每个 `{` 位置扫描，取**第一个完整 JSON 对象**；
完全没有对象才报错。解析出的对象照常交给 Pydantic 严格校验。

**回归测试**：锁定该载荷在 `json.loads` 下必然抛错（固定旧失败模式），再断言
`evaluate_answer` 正常返回。

**效果**：`is-12` 恢复（good=[88,88,88]、medium=62、off_topic=15、bad=15）。
测试 59 → 67。

---

### 3.3 优化二：模型「少给字段」→ 针对性重试（is-09）

**现象**：重跑后合格率仍是 0.9375，但失败样本换成了 `is-09`：
`ValidationError: follow_up_question Field required`——这次是模型直接漏了字段。

**要求**：不许靠「放宽 Schema」把分数刷成 1.0。于是：

- 保持 `AnswerEvaluation` 严格校验，**不改**字段默认值；
- 缺字段时**最多重试一次**，提示里明确写出缺哪个字段、什么类型（如
  `follow_up_question (str | None)`），要求只回一个 JSON；
- 重试回来的内容**完整再走一遍解析 + 校验**，没有后门；
- 值类型错误 / 越界（如 `score=120`）**不重试**（再问也好不了），直接失败；
- 失败分三类记账：provider（网络/鉴权/配额）、json（解析）、schema（校验），
  互不混淆；新增 `CompletionStats` 记录调用数、重试数、重试耗时。

**效果**：

| | 修复前 | 修复后 |
|---|---|---|
| 合格率 | 0.9375 | **1.0** |
| provider 调用 | 96（理论） | 97（多 1 次重试） |
| 重试额外耗时 | — | 2.09 秒 |

测试 67 → 83。提交 `57eec96`（仅 4 个文件）。

---

### 3.4 优化三：把容错能力铺给 job_match

**发现**：面试模块学到的容错，匹配模块一点没有——
`services/job_match_service.py` 直接把模型输出交给 LangChain parser + `model_validate`，
遇到多说话/少字段照样整条作废；它当时合格率正好 1.0，只是运气好。

**改法**：抽出 `backend/app/ai/structured_output.py`：
`extract_json_object` + `StructuredCompletion`（解析 → 校验 → 一次针对性重试 → 记账）。
它只依赖一个 `send()` 回调，所以 OpenAI SDK 和 LangChain 两种链路共用。
顺带修掉一个已知限制：带 schema 时**优先返回能校验通过的对象**，
正文草稿里的 JSON 片段不再抢戏（预检只用于挑对象，最终仍正式校验一次）。

**效果**：两个模块共用一套规则与记账；测试 108 → 126；离线重算指标不变（未改变评分结果）。
提交 `c83dc42`。

---

### 3.5 优化四：回归判定本身是错的

**发现**：原来的判定只有一个全局阈值（`--min-delta -0.02`，只看「下降」），
在三类情形上全部判断错：

| 情形 | 旧规则 | 问题 |
|---|---|---|
| 匹配误差 12.56 → 20（明显变差） | 通过 | 数字是「变大」，**真退化被放过** |
| 坏答案得分 15 → 30（评分器变糊涂） | 通过 | 越小越好的指标方向反了 |
| 样本数 25 → 24（结构变化） | **失败** | 结构性问题不该让 CI 红 |
| 好坏分差掉 0.7 分（采样噪声） | **失败** | 噪声被当成回归 |

**改法**：在 `evals/core/metrics.py` 引入 `METRIC_POLICY`，每个指标声明：

- `direction`：`higher` / `lower` / `none`（不分方向）
- `kind`：`ratio`（0–1 比率）/ `score`（0–100 分制）/ `count`（样本数）
- `action`：`gate`（失败即 CI 红）/ `warn`（打印提醒）/ `info`（只记录）
- `abs_tolerance` + `rel_tolerance`：阈值取两者**较宽者**；baseline 为 0 时**丢弃相对带**，不除零

`evals/compare.py` 逐指标判定并输出
`baseline / current / delta / threshold / decision / policy`；只有 gated 回归才返回非零。

**效果**：上面四类情形全部判对；此前被误判为回归的三项指标恢复；测试 83 → 108。
提交 `f534b18`。

---

### 3.6 优化五：用实测噪声校准阈值 + 统一输出 + provider 记账

**（a）噪声校准**：job_match 与 rag 各再跑 live（同代码、同数据），看纯采样能带来多大波动。

```bash
python -m evals.run_rag_retrieval --live
python -m evals.run_job_match --live     # 跑了两轮
```

| 套件 | 第 1 次 | 第 2 次 | 第 3 次 |
|---|---|---|---|
| rag_retrieval | 0.3974 / 0.7372 / 0.8462 / 0.6667 | **完全相同** | — |
| job_match 误差 | 12.56 | 12.00 | 12.28 |
| job_match 容差命中 | 0.76 | **0.68** | 0.76 |

两个结论：

- **RAG 是确定性的**（embedding 固定），噪声为零，阈值无需放宽；
- **容差命中率阈值过紧**：25 个样本中 1 个翻转就值 0.04，实测两次差 0.08，
  而原阈值只有 0.038 → 同代码重复跑会让 CI 变红。已放宽到 `abs=0.10, rel=0.10`；
  同时用测试固化「噪声不触发 gate，但真掉到 0.40 仍判 regression」，防止为了不误报
  就把阈值放成摆设。

**（b）统一输出口径**：`compare.report_suite()` 成为唯一入口，`run_all --compare`
不再把 `--compare` 转给各 runner，而是跑完统一打印，CLI / run_all / 单个 runner
输出同一张表（此前 CI 日志里新旧两种表并存）。

**（c）provider 记账**：job_match 有了重试却没记账，补上——`StructuredCompletion`
支持外部注入计数器，`analyze_job_match(stats=...)` 可传入，按运行汇总写进报告。
新增 `evals/provider_stats.py` 读回历史：

```
suite             created_at            mode         calls provider     json   schema  retries  rescued   failed    lat_s   retry%    fail%
interview_scoring 2026-10-30T02:20:20Z  live            97        0        0        1        1        1        0     2.09    1.03%    0.00%
job_match         2026-10-30T04:58:39Z  live            25        0        0        0        0        0        0     0.00    0.00%    0.00%

total calls=122 retries=1 rescued=1 final_failures=0 retry_latency=2.09s retry_rate=0.82%
```

**为什么需要它**：重试成功后指标是正常的，光看分数永远看不出 provider 在抖。
`retry%` 上升 = 变抖（延迟与成本上升），`provider` 非零 = 网络/密钥/配额问题，
`failed` 非零 = 重试也没救回来（合格率会同步下降）。

测试 126 → 135。提交 `bd8968a`、`bc5a1f7`、`08f64c0`。

---

## 4. 当前基线总表

| 套件 | 样本 | 关键指标 |
|---|---|---|
| job_match | 25 | 合格率 1.0 · 误差 12.56 · 容差命中 0.76 · 技能召回 0.987 / 1.0 |
| rag_retrieval | 26 查询 / 186 段 | recall@1 0.397 · recall@3 0.737 · recall@5 0.846 · mrr 0.667 |
| interview_scoring | 16 | 合格率 1.0 · 区分度 1.0 · 档位顺序 1.0 · 好坏分差 70.06 · 好/中/偏题/差 = 88/55/18/16.5 |

单元测试 **135 passed**；CI 序列（`run_all --compare` + 三个 `compare --check`）全部 exit=0。

## 5. 经验教训

1. **快照必须与数据集对齐**。样本没预测值时，指标不会报错，只会给你一个看起来合理的错数字。
   覆盖率（如 `schema_valid_rate`、`scored_samples`）必须是显式的指标。
2. **`write_predictions` 每次都会覆盖 `*.latest.jsonl`，而基线报告里记的正是这个 `latest` 路径**。
   曾因一次离线重算覆盖了 latest，导致 promote 出来的基线元数据变成 `mode=offline`、
   notes 丢失。现在 promote 前会先确认报告的 `mode`。
3. **失败样本会静默消失**。两次 live 各失败 1/16，说明 provider 随机失效率约 6%，
   没有重试就永远救不回来。
4. **一个全局阈值判回归必然出错**：方向、量纲、严重性三个维度都要分开处理。
5. **阈值要按实测噪声定**，不能拍脑袋。第一次定的 0.038 就是被真实运行推翻的。
6. **重试会掩盖问题**。重试让分数变漂亮，所以抖动必须单独记账、单独看趋势。
7. **不要为了指标好看放宽 Schema**。缺字段就重试并明确告知模型，而不是给默认值。

## 6. 提交历史

| SHA | 内容 |
|---|---|
| `667d687` | 评测数据集与三套 live 基线、离线测试、第三方语料来源说明 |
| `57eec96` | 面试引擎：JSON 解析容错 + 缺字段针对性重试 + 失败分类记账 |
| `f534b18` | 逐指标回归策略（direction/kind/action/容差），compare 重写 |
| `c83dc42` | 共享 `structured_output`，job_match 接入同一容错路径 |
| `b6dfe5f` | provider 端点可配置、`.env` 镜像到环境变量的修复、CI evals job |
| `225496e` | promote 面试基线（合格率 1.0）及对应快照与历史报告 |
| `bd8968a` | 按实测噪声校准容差 + 统一 compare 输出口径 |
| `bc5a1f7` | job_match 记账 + `provider_stats` 历史汇总 |
| `08f64c0` | 三次 noise-sample live 产物留档 |

## 7. 待办与建议

- **噪声样本偏少**：容差阈值目前由 3 次 job_match 运行支撑，建议再攒几次再复核；
  RAG 既然是确定性的，其阈值可以收紧而不必担心误报。
- **provider 数据只有 2 次运行**（122 次调用、1 次重试），趋势意义有限，
  建议每次改动后跑 live 并保留报告。
- ~~**测试输出被日志污染**~~（已处理）：新增 `tests/__init__.py`，把 root logger 挂上
  `NullHandler` 并设为 `CRITICAL`；命令改为 `python -m unittest discover -s tests -t .`
  （`-t .` 才能让 `tests` 作为包被导入、初始化生效）。CI 与 README 已同步。
- ~~**评测运行会覆盖 `latest`**~~（已加护栏）：`evals/core/reporting.py` 的
  `promotion_blockers()` 检查三件事——`mode` 必须是 `live`、`scored_samples`
  等于 `samples`、`schema_valid_rate` 为 1.0；`compare --promote` 命中任何一条就
  拒绝（exit 2）并打印原因，只有 `--force` 才放行并同时打印 WARNING。
- **仓库里 `job_match` / `rag_retrieval` 的 `latest` 已经是 offline**：加护栏时才
  发现，两个 `*.latest.json` 的 `mode` 都是 `offline`（对应的 `*.baseline.json`
  仍是 live，指标不受影响）。也就是说下次改完代码想 promote，必须先跑一次 live
  刷新 latest——这正是护栏要拦的场景。
