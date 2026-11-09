# 项目定位

为准备求职面试的人，用简历分析和模拟面试，发现能力短板并练习表达。

**不做：** 简历代写；招聘方筛选；无边界的通用聊天。

```text
React --HTTP 同步：鉴权、校验、上传、建任务、返回 job_id--> FastAPI
FastAPI --同步写入--> PostgreSQL（任务/结果；知识库向量用 pgvector）
FastAPI --同步 XADD--> Redis Streams --异步消费--> Worker
Worker --异步处理：解析、向量化/评分、写回状态结果--> 模型/Embedding Provider
文件：local / MinIO / S3 可切换；API 保存，worker 按 object_key 读取
```

当前核心是可追踪的求职准备工作流，不是自主规划和调用工具的 Agent。

质量基线：`667d687`（后续完整 live run 晋升保护：`72de87d`）。
任务可靠性：`3117466`（原子抢占、有限重试、租约恢复）。
