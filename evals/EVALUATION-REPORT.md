# 评测报告（基线）

- **更新时间**：2026-10-29
- **代码版本**：`git sha 4a0af927eeb0bddd8b9a9e12687ffab4ccdf2dd7`
- **当前状态**：数据集已扩充，**下面所有数字都来自扩充前的 3 样本版本，已不可与当前数据集直接比较**。

| Suite | 状态 | 样本量（本报告） | 样本量（当前数据集） |
|---|---|---|---|
| `job_match` | ⚠️ 基线已过期，需重新 `--live` + `--promote` | 3 | 25 |
| `interview_scoring` | ⚠️ 基线已过期，需重新 `--live` + `--promote` | 3 | 16 |
| `rag_retrieval` | ⏸ 语料与查询已就绪（186 chunks / 26 queries），尚无 embedding 快照 | 0 | 26 |

> **2026-10-29 数据集扩充**：`evals/raw_docs/` 加入 25 份文档（9 份第三方技术文档 + 16 份自建
> 岗位/简历/笔记材料），`rag_corpus.jsonl` 186 块、`rag_queries.jsonl` 26 条 anchor 标注查询、
> `job_match.jsonl` 25 组、`interview_scoring.jsonl` 16 题 × 4 档答案。组成与标注规则见
> `datasets/README.md`。旧的 3 样本快照与报告已移到 `snapshots/archive/`、`reports/archive/`，
> **不要再拿它们算 delta**：`job_match` 重新编号后，旧预测对应的是另一对 JD/简历，复用会静默
> 比错（实测 MAE 11.7 → 34.3 的假跌）。重新 `--live` 并 `--promote` 之后，本节数字整体作废。

---

## 1. 是不是"自己模拟测试"？

**不是 mock，是真实 API 调用。** 两种模式要分清：

| 模式 | 是否调用模型 | 用途 | 使用情况 |
|---|---|---|---|
| `--live` | **是**，真实请求 | 生成预测快照 | 两个 suite 各跑了一次，共 **15 次成功调用** |
| 离线（默认） | 否，读快照重算指标 | CI / 改指标 / 改标签 | 单元测试验证指标数学，不产生新数据 |

本次实际消耗：

| Suite | 调用内容 | 次数 |
|---|---|---|
| `job_match` | 简历/JD 匹配分析 | 3 次 chat |
| `interview_scoring` | 3 组 ×（好答案重复 3 次 + 坏答案 1 次） | 12 次 chat |
| **合计** | | **15 次** |

模型：聊天 `deepseek-chat`（`.env` 中 `ACTIVE_CHAT_PROVIDER=deepseek`）。

> 曾用项目文档切出的 48 段合成语料跑过一轮检索评测（含三个 embedding 模型对照），**已按你的要求连同语料、快照、报告一并删除，其结论不作为证据保留**。检索评测的流程代码和 `evals/build_corpus.py` 保留，等真实语料后可直接复用。

---

## 2. 测了什么

| Suite | 测的是什么 | 指标 |
|---|---|---|
| `job_match` | 给定简历 + JD，模型输出的**匹配分**和**技能抽取**是否可信 | `schema_valid_rate`、`score_mae`、`score_within_tolerance`、`matched_skill_recall`、`missing_skill_recall` |
| `interview_scoring` | 评分器能否区分好/坏答案，且对同一答案多次打分是否稳定 | `schema_valid_rate`、`discrimination_rate`、`mean_gap`、`median_good_score`、`score_spread_median` |
| `rag_retrieval` | 向量检索能否把正确片段排在前面（**只测检索，不测生成**） | `recall@1/3/5`、`mrr` —— **待语料** |

指标定义见 `evals/core/metrics.py`。

**没测的部分**（说明以免误导）：回答的**正确性 / 依据充分性 / 引用准确性**完全没测；语音链路、任务队列、PDF 导出未纳入评测。

---

## 3. 测试用例是怎么生成的？存在哪里？

**用例是手写构造的，不是程序自动生成，也不是真实用户数据。**

| 数据集 | 来源 | 条数 |
|---|---|---|
| `evals/datasets/job_match.jsonl` | 复用仓库里的 `sample_jd.txt` / `sample_resume.txt`，改写为 3 组「JD × 简历」对照：强相关 / 弱相关 / 强匹配 | 3 |
| `evals/datasets/interview_scoring.jsonl` | 手写技术面试题 + 一个强答案 + 一个明显差的答案 | 3 组 |
| `evals/datasets/rag_*.jsonl` | **已删除**，等真实语料 | — |

存放位置：

```
evals/
  datasets/      ← 样本与标签（人工维护，需替换为真实数据）
  snapshots/     ← 模型原始预测（机器生成，可复现）
  reports/       ← 指标结果（*.latest.json、*.baseline.json、带时间戳历史）
  core/          ← 数据集加载、指标、报告、快照（无模型调用）
  build_corpus.py ← 用生产 split_text 把文档切成语料（等你给素材后启用）
  run_*.py       ← 各 suite 入口
```

**标签的可信度是这套评测最大的软肋**：`job_match` 的 `score_center`（60 / 38 / 85）是我凭经验估的，不是你认可的基准。所以 `score_mae = 11.67` 的真实含义是"**模型与我（一个大语言模型）的估计相差约 12 分**"，不是"模型准确率"。**必须换成你自己的判断，这个数字才有意义。**

---

## 4. 样本级明细（真实快照数据）

### 4.1 job_match（3 条）

| ID | 场景 | 标签中心分 | 模型给分 | 绝对误差 | 容差 ±12 |
|---|---|---|---|---|---|
| jm-01 | AI 产品实习 JD × 普通 CS 学生简历 | 60 | 72 | 12 | 通过 |
| jm-02 | 后端工程师 JD × 同一份学生简历（弱相关） | 38 | 22 | 16 | **未通过** |
| jm-03 | ML 工程师 JD × 资深 ML 简历（强匹配） | 85 | 92 | 7 | 通过 |

技能抽取（全部命中）：

| ID | 模型给出的 matched | 模型给出的 missing |
|---|---|---|
| jm-01 | Python、LLM applications、Prompt engineering、Communication skills、Experience building AI applications、Machine Learning、Data Analysis | RAG、User research、Product thinking、Vector databases / embeddings |
| jm-02 | Python | FastAPI、Django、PostgreSQL、SQL schema design、Query tuning、Docker、CI/CD、AWS deployment、REST API development |
| jm-03 | Python、PyTorch、Deep learning training/eval、End-to-end ML pipelines、Experiment tracking、Reproducibility、Inference deployment、FastAPI、Docker | Product team collaboration、Large-scale dataset handling |

观察：模型倾向**多列技能项**（jm-01 列了 7 个 matched、4 个 missing），其中"Product thinking""Vector databases / embeddings"属于 JD 未明写的推断项——影响前端展示密度，也说明需要约束输出条数。

### 4.2 interview_scoring（3 组 × 好答案重跑 3 次）

| ID | 问题类型 | 好答案三次得分 | 坏答案得分 | 差值 |
|---|---|---|---|---|
| is-01 | 异步任务防重复处理 | 92 / 92 / 92 | 15 | 77 |
| is-02 | 如何衡量检索质量 | 82 / 82 / 82 | 15 | 67 |
| is-03 | 文档内注入指令怎么办 | 92 / 92 / 92 | 15 | 77 |

---

## 5. 汇总结果与统计解读

| Suite | 指标 | 结果 | 统计上说明什么 |
|---|---|---|---|
| job_match (n=3) | schema_valid_rate | 1.00 | 结构化输出稳定 |
| | score_mae | **11.67** | 与我的估计平均差 11.7 分 |
| | score_within_tolerance | 0.67 | 3 条里 2 条落在 ±12 内 |
| | matched_skill_recall | 1.00 | 标注技能全被覆盖 |
| | missing_skill_recall | 1.00 | 标注缺口全被识别 |
| interview_scoring (n=3) | schema_valid_rate | 1.00 | — |
| | discrimination_rate | 1.00 | Wilson 95% CI ≈ **[0.44, 1.00]** |
| | mean_gap | 73.67 | 好/坏答案分差很大 |
| | median_good_score | **92** | 疑似评分虚高 |
| | score_spread_median | 0.0 | 三次重跑完全一致 |

**必须诚实说明的统计问题**：n=3 时统计功效接近于零，`discrimination_rate = 1.00` 的 95% CI 下界只有 0.44；想检测出 MAE 改善 5 分需要几十条样本，不是 3 条。这两个 suite 现在的价值是**证明链路可跑、有了可对比的基线**，不是证明效果好。

---

## 6. 过程中发现的两个问题

### 6.1 Provider 密钥在非容器路径下拿不到（已修复）

`job_match` 首次 live 运行 `schema_valid_rate = 0.0`，三条全部报错：

```
RuntimeError: Set DEEPSEEK_API_KEY to enable provider 'deepseek'
```

根因：`MODEL_PROVIDERS` 注册的 provider 用 `os.getenv()` 读密钥，而 pydantic-settings 读 `.env` **不会写进 `os.environ`**，所以只要不走 Docker Compose，就必然拿不到 key。这与 `AI-AGENT-ENGINEERING-ASSESSMENT.md` 里标记的 P1 是同一个问题。

修复（`backend/app/core/config.py`）：在 `get_settings()` 里把 `.env` 镜像进环境变量，且不覆盖真实环境变量。

```python
@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    env_file = Settings.model_config.get("env_file")
    if isinstance(env_file, str) and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    return settings
```

修复后 `schema_valid_rate` 从 0.0 → 1.0，56 项单元测试仍全绿。**这条本身是简历上能讲的一句话。**

### 6.2 评分分布的两个可疑信号

- **好答案中位数 92 分**：三个好答案分别得 92 / 82 / 92，整体偏高，评分器偏宽松。
- **坏答案全部恰好 15 分**：三段完全不同的差答案都拿到 15。代码里只有 `ge=0, le=100` 约束、没有下限截断，所以这是模型行为——对"明显差"的答案锚定在同一低分，**无法区分"很差"和"中等偏差"**。要改进需补梯度样本（中等答案、答非所问、正确但不完整）。

---

## 7. 这些数字现在能写进简历吗？

| 内容 | 结论 |
|---|---|
| "端到端跑通了 AI 能力的自动化评测，含快照复现与 CI 回归" | ✅ **可以写**（事实，可复现） |
| "结构化输出有效率 100%" | ⚠️ 可以写，但要注明 n=3，属于烟雾测试 |
| "修复非容器启动路径下 provider 密钥读取失效" | ✅ **可以写**（有根因、有修复、有测试） |
| "检索召回提升 X%" | ❌ **目前一条都没有**（语料已清空，等真实数据） |
| "匹配准确率 / 评分准确率提升 X%" | ❌ **不要写**。无标注基准、无对照实验、无 delta |

**能写进简历的量化结论必须来自"对照实验的 delta"**，例如换 embedding 模型后 `MRR 0.745 → 0.840`。要拿到它，先得有真实语料。

---

## 8. 复现方式

```bash
conda activate ai-job-assistant
cd /Users/caoyuerong/Documents/CU-MSc/self-project/ai-job-assistant

# 离线：从快照重算，零成本、零密钥（CI 就是这么跑的）
python -m evals.run_all --compare        # rag_retrieval 会自动跳过

# 生成新快照（会花钱）
python -m evals.run_job_match --live
python -m evals.run_interview_scoring --live --repeats 3

# 与基线对比 / 提升为基线
python -m evals.compare --suite job_match
python -m evals.compare --suite job_match --promote

# 评测指标与数据集完整性单测
python -m unittest tests.test_evals_offline -v
```

---

## 9. 下一步（按 ROI 排序）

1. **给检索评测一份真实语料**（你来做，唯一我替代不了的一步）。给我 10–20 份真实文档——简历、JD、面经、岗位描述、技术笔记都行；或者导出 `document_chunks` 表的真实片段。我用 `evals/build_corpus.py` 切分 + 生成查询标注，跑出 `recall@k` / `MRR` 基线。
2. **扩 job_match 到 20–30 条，并用你自己的判断覆盖 `score_center`**。这一步不做，`score_mae` 永远只是"模型同意我"的度量。
3. **加梯度化的面试样本**：中等答案、答非所问、正确但简略，检验评分器能否真正分档（现在的 15 分锚定说明它不能）。
4. **做对照实验拿 delta**：embedding 模型、分块参数、job_match prompt 改写。每做完一次 `--compare` 就有可引用的数字。
5. **对同一组样本重复跑 2–3 次**，把单次运行的方差测出来，delta 才有置信度。
6. **把回答质量纳入评测**：需要 LLM-as-judge 或人工标注的 Q&A 集，目前完全空白。
