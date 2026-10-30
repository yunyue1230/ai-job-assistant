# 语料来源与许可

本目录下的文档分两类，用途不同：

## 一、第三方技术文档（`t*.md` / `t*.rst`）

从官网或仓库原样拉取（仅去除 YAML frontmatter、短代码和空行），用于给语料提供
**真实的长文档、术语密集文本和干扰项**。拉取时间：2026-10-29。

| 文件 | 来源 | 许可 |
|---|---|---|
| `t01_pgvector_readme.md` | https://github.com/pgvector/pgvector/blob/master/README.md | PostgreSQL License |
| `t02_fastapi_dependencies.md` | https://github.com/fastapi/fastapi/blob/master/docs/en/docs/tutorial/dependencies/index.md | MIT |
| `t03_fastapi_sql_databases.md` | https://github.com/fastapi/fastapi/blob/master/docs/en/docs/tutorial/sql-databases.md | MIT |
| `t05_fastapi_docker.md` | https://github.com/fastapi/fastapi/blob/master/docs/en/docs/deployment/docker.md | MIT |
| `t06_k8s_nodes.md` | https://github.com/kubernetes/website/blob/main/content/en/docs/concepts/architecture/nodes.md | CC BY 4.0 |
| `t08_vllm_readme.md` | https://github.com/vllm-project/vllm/blob/main/README.md | Apache-2.0 |
| `t09_sqlalchemy_readme.rst` | https://github.com/sqlalchemy/sqlalchemy/blob/main/README.rst | MIT |
| `t10_faiss_getting_started.md` | https://github.com/facebookresearch/faiss/wiki/Getting-started | MIT |
| `t12_langchain_readme.md` | https://github.com/langchain-ai/langchain/blob/master/README.md | MIT |

说明：

- `t06` 来自 Kubernetes 官网文档仓库，许可为 CC BY 4.0，**转载需保留署名**。本文件即署名记录。
- 曾拉取 Redis 仓库的 README，因其许可为 RSALv2/SSPL 而非开源许可，已删除，避免把受限文本提交进公开仓库。
- 这些文档只用于本地评测。如果要把仓库公开，建议把整个 `raw_docs/` 与派生的
  `datasets/rag_corpus.jsonl` 加入 `.gitignore`，或保留本文件作为署名。
- 语料会发送给外部 embedding 服务商，因此**不要**在这里放含个人身份信息或公司机密的文档。

## 二、自建人物材料（`w*.md`）

岗位描述、简历、面经笔记、项目设计文档是为评测手写的**虚构人物**材料，不对应任何真实
个人或公司，可以自由修改：

| 文件 | 类型 | 作用 |
|---|---|---|
| `w01`–`w06` | 6 份 JD | 后端、LLM 应用、推荐算法、AI 产品实习、数据分析、平台/SRE |
| `w07`–`w10` | 4 份简历 | 3 年后端、3 年算法、应届硕士、关键词堆砌（负样本） |
| `w11`–`w13` | 3 份笔记 | 后端面经、RAG 检索评测、机器学习八股 |
| `w14` | 1 份设计文档 | 本项目的架构、队列、幂等、评测说明 |
| `w15`–`w16` | 2 份退化 JD | 极短模糊 JD、不限定技术栈的泛化 JD |

同一批 `w*` 文件同时供三个 suite 使用：`rag_retrieval` 的语料、`job_match` 的
JD × 简历配对、`interview_scoring` 的岗位背景。这样「检索到的内容」和「面试考察的内容」
是同一批素材，评测结果和面试叙事不会脱节。
