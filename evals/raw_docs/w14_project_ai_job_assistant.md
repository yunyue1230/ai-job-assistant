# AI 求职助手 — 系统设计文档 v0.4

## 1. 目标

给求职者一个闭环：上传简历 → 匹配岗位 JD → 建知识库 → 模拟面试 → 生成报告。所有 AI 能力都建立在可替换的 provider 之上，聊天模型和 embedding 模型分开配置。

## 2. 总体架构

前端 React（Vite），后端 FastAPI，PostgreSQL 存业务数据和向量，Redis Streams 做任务队列，MinIO 或本地文件系统存原始文件。三个常驻进程：

1. `api`：HTTP 接口，负责鉴权、上传、查询；
2. `analysis-worker`：消费简历解析与匹配任务；
3. `reminder-worker`：消费面试日程提醒。

API 里不做重活。上传接口只做三件事：校验大小与类型、把文件写进存储、往 Redis Stream 里塞一条消息，然后立刻返回 job id。前端轮询 job 状态。

## 3. 数据模型

核心表：

- `users`：账号、密码哈希、时区；
- `resumes`：文件元信息、存储键、解析后的文本；
- `analysis_jobs`：任务状态机 `queued → processing → succeeded / failed`，带重试次数和错误信息；
- `document_chunks`：知识库切片，字段为 `id, document_id, chunk_index, text, embedding, metadata`，embedding 是 `vector(1024)`；
- `interview_sessions` / `interview_turns`：会话与逐轮问答，含评分与改进建议；
- `schedules`：面试日程与提醒时间。

## 4. 任务队列

用 Redis Streams 的 consumer group，而不是简单的 list。原因是 group 支持 ack 和 pending 列表，worker 崩了之后可以用 `XAUTOCLAIM` 把超时未 ack 的消息重新分配，不会丢任务。

幂等策略：

- 入队时生成 job id，同一条消息重投时 id 不变；
- worker 领取任务时执行 `update analysis_jobs set status='processing' where id=? and status='queued'`，影响行数为 0 就直接跳过；
- 处理结果写入用唯一键约束兜底，重复写不会产生重复记录；
- 超过最大重试次数进死信，前端显示「可手动重试」。

踩过的坑：早期用 `select ... if not processing then update` 两步走，两个 worker 并发时都读到 queued，都去处理，同一份简历被解析了两次，还重复扣了模型调用。改成条件更新之后问题消失。

## 5. 知识库与检索链路

1. 上传 txt / md / pdf / docx；
2. `extract_document_pieces` 按格式抽文本，PDF 逐页抽，保留页码；
3. `split_text` 按字符窗口切块，默认 1000 字符、150 重叠，切分时优先在句号、分号、空格处断开，避免切在句子中间；
4. 批量调 embedding（默认批大小 10），写入 `document_chunks`；
5. 检索用 pgvector 的余弦距离 `<=>`，配 HNSW 索引，`ef_search` 按延迟要求调；
6. 召回 top-k 后拼进上下文，附带来源标题和页码。

多租户隔离靠 `document_id` 归属校验，检索 SQL 里带 `where user_id = ?`，不允许跨用户召回。

## 6. 评测体系

三个 suite，各自独立数据集，live 模式只调用一次 provider，把原始预测存成快照，之后改指标、改标签、跑 CI 全部离线复算，不花钱。

- `job_match`：JD × 简历，测匹配分误差（MAE）、容差命中率、技能与缺口抽取召回；
- `rag_retrieval`：固定语料 + 标注查询，测 recall@1/3/5 与 MRR，用精确余弦排序，所以是线上的理论上限；
- `interview_scoring`：同一问题下强/中/弱答案的分数梯度，测区分度、平均分差、重复打分稳定性。

语料用生产同一个 `split_text` 构建，标签用原文短引用而不是 chunk id，这样改切块参数后还能对比。

## 7. 已知问题

- 检索只做了向量一路，没有 BM25，专有名词查询召回差；
- 没有 rerank，top-k 直接进上下文，长文档容易超窗口；
- 简历解析依赖文本抽取，扫描版 PDF 抽不出来，需要 OCR；
- 面试评分没有参考答案，模型容易给高分（好答案常被打到 90 以上），需要加校准；
- 提醒任务没有分布式锁，多副本部署时会重复推送。

## 8. 下一步

1. 加混合检索与 RRF 融合；
2. 加 rerank（cross-encoder），先离线验证 nDCG 收益；
3. 把评测集扩到 20–30 组，用真实简历和 JD 替换种子数据；
4. 面试评分引入分档标准（优秀/合格/不合格）做锚定，压制虚高。
