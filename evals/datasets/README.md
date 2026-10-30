# 数据集说明（dataset card）

三个 suite 的数据都在这个目录。所有条目都是**人工标注**的，机器只负责生成预测
（`snapshots/`）和指标（`reports/`）。

| Suite | 文件 | 样本量 | 主要指标 |
|---|---|---|---|
| `rag_retrieval` | `rag_corpus.jsonl` + `rag_queries.jsonl` | 186 chunks / 25 份文档，26 条查询 | `recall@1/3/5`、`mrr` |
| `job_match` | `job_match.jsonl` | 25 组 JD × 简历 | `schema_valid_rate`、`score_mae`、`score_within_tolerance`、`matched_skill_recall`、`missing_skill_recall` |
| `interview_scoring` | `interview_scoring.jsonl` | 16 个问题 × 4 个答案档位 | `schema_valid_rate`、`discrimination_rate`、`mean_gap`、`median_good_score`、`score_spread_median`、`tier_order_rate` |

---

## 1. `rag_corpus.jsonl` / `rag_queries.jsonl`

### 语料

由 `python -m evals.build_corpus` 用生产同一个 `split_text` 切出来，**不是手写近似**：
`chunk_size=1000`、`overlap=150`，186 个块，平均 937 字。

| 组别 | 文档数 | 块数 | 内容 |
|---|---|---|---|
| 第三方技术文档 | 9 | 161 | pgvector、FastAPI（依赖/数据库/Docker）、Kubernetes Nodes、vLLM、SQLAlchemy、FAISS、LangChain |
| 自建人物材料 | 16 | 25 | 6 份 JD、4 份简历、3 份面经笔记、1 份设计文档、2 份退化 JD |

来源与许可见 `raw_docs/SOURCES.md`。第三方文档只做了 frontmatter/短代码清理，用来提供
长文档、术语密集文本和干扰项；自建材料是虚构人物，可自由修改。

`chunk_id` 格式为 `<目录前缀>-<文件名哈希>-<序号>`，例如 `RAT01-2EE-008`。哈希后缀是必须的：
同一个目录里多份文档的前几位字符往往相同，早期只用 `父目录[:2] + 文件名[:3]` 会生成重复
`chunk_id`，导致标注指向错误文本（已修，并在 `build()` 里加了重复检测）。

### 查询

26 条，全部用 `relevant_anchors`（原文短引用）标注，**不用 chunk id**。

| 类型 | 条数 | 目的 | 当前（词法基线） |
|---|---|---|---|
| `direct` 直接事实 | 10 | 基本召回是否成立 | R@1 0.40 / R@5 0.90 |
| `paraphrase` 同义改写 | 5 | 语义匹配鲁棒性 | R@1 0.70 / R@5 1.00 |
| `distractor` 干扰项 | 7 | 排序质量（最能区分 embedding） | R@1 0.57 / R@5 0.71 |
| `cross_chunk` 跨块 | 4 | 能否一次捞全 | R@1 0.13 / R@5 0.71 |

「当前」列是我用 BM25（英文按词、中文按字 bigram）跑的**词法**基线，不是 embedding 基线，
只用来说明这批查询没有被关键词一键解决：整体 R@1 0.46 / R@5 0.84 / MRR 0.65，留了足够
的提升空间给向量模型，也保证了「换 embedding 模型」这件事有可测的 delta。

标注规则：

- anchor 取 8–20 个连续字，不跨标点，做空白折叠 + 小写后做子串匹配；
- anchor 必须**唯一指向**想测的那段内容，不能只落在「两份文档都提到这个词」上；
- 匹配不到时 `resolve_relevant_chunks()` 直接抛错，不静默记 0 分——静默的 0 分比崩溃更难查。

因为 `overlap=150`，有些 anchor 正好落在两个块的重叠区，会解析出 2 个相关块
（如 `rq-10`）。这是重叠切块的正常结果，也让 `recall@1` 的天花板变成 0.5；
跨块查询（`rq-22`–`rq-25`）本身就是 2–3 个相关块，`recall@5` 要求全部命中。

**没有「无答案型」查询**：现有指标只有 recall/MRR，没有拒答率或幻觉率，加进去只会把
recall 拉低并被误读。要测拒答，先扩展 `evals/core/metrics.py`。

---

## 2. `job_match.jsonl`

25 组，按场景分档，`expected` 里是**我读这一对材料之后的判断**，不是模型输出：

| 档位 | 条数 | 分数区间 | 说明 |
|---|---|---|---|
| `strong` 强匹配 | 3 | 70–88 | 同岗同年限，主技术栈逐条对上 |
| `medium` 中等 | 6 | 40–72 | 技术栈部分重叠，或年限/深度差一截 |
| `weak` 弱相关 | 7 | 25–38 | 跨领域投递 |
| `keyword` 关键词堆砌 | 4 | 20–30 | 简历列满关键词、经历全是套话（`w10`），用来测模型会不会被词面骗 |
| `ambiguous` 退化 JD | 5 | 40–65 | 极短 JD / 不限定技术栈，容差放宽到 15–20 |

标注约定：

- `score_center` 是 0–100 的**人类参考分**，默认容差 12；`score_tolerance` 现在会被
  `score()` 逐条读取，模糊 JD 用 20，强匹配用 10；
- `must_match_skills` 只写简历里**有可验证证据**的技能，`must_missing_skills` 只写 JD 明确要求
  且简历完全没有的，各 2–3 项，避免把「模型没列全」误判成「抽错了」；
- `band` / `notes` / `source` 是给人和脚本看的，评分不读。

这些分数可以直接改。改完不用重跑 `--live`，用旧快照离线复算即可：

```bash
python -m evals.run_job_match --snapshot evals/snapshots/job_match.deepseek-chat.latest.jsonl
```

---

## 3. `interview_scoring.jsonl`

16 个问题，每个问题**四个档位**。原来的 3 组只有「好 / 极差」，坏答案全被打 15 分，
说明评分器只会二分；四档是为了看它能不能分出中间层。

| 字段 | 档位 | 写作要求 |
|---|---|---|
| `good_answer` | 强 | 完整、有机制细节、提到边界条件，重复打 3 次看稳定性 |
| `medium_answer` | 中 | 方向正确但简略：只说做法不说为什么，缺权衡 |
| `off_topic_answer` | 答非所问 | 回答的是同一领域里的另一个问题（如问幂等答监控扩容） |
| `bad_answer` | 明显错误 | 自信地给出错误结论（如「不需要设过期时间」） |

问题覆盖后端（并发幂等、Stream 重投、联合索引、缓存一致性、分布式锁、顺序消费、限流、
深分页、MVCC 幻读）和算法/RAG（检索评测、切块、HNSW 调参、prompt injection、
embedding 选型、过拟合、离线涨线上不涨），和 `job_match` 用的是同一批岗位素材。

新增指标 `tier_order_rate` = 「good > medium > max(off_topic, bad)」成立的比例；
`median_medium_score` / `median_off_topic_score` / `median_bad_score` 给出每档的实际分位，
方便定位是哪一档塌了。老快照（只有 good/bad）跑离线复算时，这些新指标会自动缺席，不会报错。

---

## 4. 已知局限

1. **语料语言不均衡**：第三方文档 161 块是英文，自建中文材料只有 25 块（13%）。中文查询要
   在英文为主体的大语料里定位，比真实场景更难；补几份中文笔记就能改善。
2. **人物材料是虚构的**：JD/简历写得像真的，但不是你投过的岗位。`score_center` 也只是我的
   判断，最终应以你自己的判断为准。
3. **`score_center` 是单点评分**：没有多人标注，也就没有标注者一致性。25 组只能说「贴近我的
   直觉」，不能说「贴近人类共识」。
4. **重叠导致多块标注**：见上文，`recall@1` 的天花板在某些查询上是 0.5。
5. **旧基线已归档**：`snapshots/archive/` 与 `reports/archive/` 里是 3 样本时代的快照和报告，
   保留只为追溯，**不要再拿它算 delta**。原因不只是样本量变了：`job_match` 的 id 重新编号后，
   旧预测对应的是另一对 JD/简历，复用旧快照会静默地拿错答案去比，指标会假跌（实测
   `score_mae` 从 11.7 跳到 34.3）。现在 CI 跑 `run_all` 会明确报「没有快照」，而不是给出错误数字。

## 5. 重新生成

```bash
conda activate ai-job-assistant

# 语料（默认读取 evals/raw_docs 下所有 .md/.txt/.rst）
python -m evals.build_corpus --chunk-size 1000 --overlap 150

# 校验标注：任何 anchor 解析不到都会直接报错
python -m unittest tests.test_evals_offline

# 三个 suite 的真实调用，各跑一次，然后提升为基线
python -m evals.run_job_match --live
python -m evals.run_rag_retrieval --live
python -m evals.run_interview_scoring --live --repeats 3
python -m evals.compare --suite job_match --promote
python -m evals.compare --suite rag_retrieval --promote
python -m evals.compare --suite interview_scoring --promote
```
