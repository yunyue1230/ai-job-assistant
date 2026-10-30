# 曹予 — 计算机科学硕士（2026 届）

香港 | 手机 137-0000-0000 | 邮箱 caoyu@example.com | GitHub github.com/example

## 教育背景

- 香港某大学 计算机科学 硕士 2025.09–2026.11（预计），GPA 3.6/4.0
- 内地某 211 大学 软件工程 本科 2021.09–2025.06，GPA 3.5/4.0

## 实习经历

### 2024.07–2024.12 某互联网公司 后端开发实习生

- 参与内部工单系统的接口开发，用 FastAPI 写了 14 个接口，其中 6 个是分页查询与导出。
- 跟着导师做过一次慢查询优化：给订单表加了 (user_id, created_at) 联合索引，列表接口从 1.8s 降到 240ms。
- 写了接口的单元测试，覆盖率从 43% 提到 71%。

### 2025.01–2025.05 某创业团队 算法实习生（远程）

- 用 LangChain + OpenAI API 搭了一个文档问答 demo，支持上传 PDF、切块、检索、带引用回答。
- 做了一版简单的检索评测：手工标了 30 条问题和对应片段，测了 recall@3，换了两种 embedding 后 recall@3 从 0.62 提到 0.74。
- 发现切块大小对结果影响很大，1000 字切成 600 字之后，答案里缺上下文的情况明显减少。

## 项目经历

**AI 求职助手（个人项目，2025.06–至今）**
一个帮助求职者改简历、做模拟面试的 Web 应用。后端 FastAPI + PostgreSQL + pgvector，异步任务用 Celery，前端 React。已经跑通上传简历、解析、匹配 JD、模拟面试打分、导出报告几条链路。自己写了离线评测脚本，用固定样本算匹配分的平均误差。

**图像超分辨率（课程项目）**
用 PyTorch 复现了 SRCNN 和一个简化的 diffusion 版本，在 DIV2K 子集上做对比，PSNR 提升 1.2dB。做过数据增强和消融实验。

## 技能

- 语言：Python（熟练）、JavaScript/TypeScript（能用）、Java（课程水平）、SQL
- 后端：FastAPI、PostgreSQL、Redis（了解基本用法）、Celery、Docker
- 机器学习：PyTorch、scikit-learn、pandas、numpy
- LLM：OpenAI API、LangChain、prompt engineering、RAG 基础
- 工具：Git、Linux、Docker Compose

## 自我评价

工程和算法都做过一点，偏工程落地。习惯把结论写成可复现的脚本，不太喜欢「看着还行」这种判断。目前在香港，愿意回内地工作。
