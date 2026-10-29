# Local Docker 操作手册

本文说明如何在本机使用 Docker Compose 运行 Interview Guide。范围仅限本地开发和功能试用，不包含云部署或生产环境加固。

## 1. 准备环境

安装并启动 Docker Desktop，然后在终端进入项目目录：

```bash
cd "/Users/caoyuerong/Documents/CU-MSc/self-project/ai-job-assistant"
docker --version
docker compose version
docker info
```

如果 `docker info` 无法连接，请先启动 Docker Desktop。

## 2. 创建并配置 `.env`

首次配置时复制模板，不会覆盖已存在的本地 `.env`：

```bash
cp -n .env.example .env
```

`.env` 中的 API Key 只供本机运行的后端和 Worker 使用。不要提交 `.env`，也不要在聊天或日志中分享 Key。应用不再接收或保存网页用户填写的 API Key。

聊天、Embedding 和语音是相互独立的配置。请分别选择兼容的服务商，并填写它们各自的凭证。

### 2.1 聊天模型

简历分析、文本模拟面试、知识库问答和语音面试回答使用活动聊天服务商。默认示例是 DeepSeek：

```dotenv
ACTIVE_CHAT_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的DeepSeek密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
```

也可以在 `MODEL_PROVIDERS` 注册多个 OpenAI-compatible Chat Completions 服务，并由 `ACTIVE_CHAT_PROVIDER` 选择其中一个。每项填写接口地址、模型名和对应的环境变量名；Key 本身单独放在 `.env` 中：

```dotenv
ACTIVE_CHAT_PROVIDER=dashscope
MODEL_PROVIDERS={"deepseek":{"base_url":"https://api.deepseek.com","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},"openai":{"base_url":"https://api.openai.com/v1","model":"gpt-4o-mini","api_key_env":"OPENAI_API_KEY"},"dashscope":{"base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","model":"qwen3.5-flash","api_key_env":"DASHSCOPE_API_KEY"},"kimi":{"base_url":"https://api.moonshot.cn/v1","model":"moonshot-v1-8k","api_key_env":"MOONSHOT_API_KEY"},"glm":{"base_url":"https://open.bigmodel.cn/api/paas/v4","model":"glm-4-flash","api_key_env":"GLM_API_KEY"},"lm-studio":{"base_url":"http://host.docker.internal:1234/v1","model":"your-local-model","api_key_env":"LM_STUDIO_API_KEY"}}
DASHSCOPE_API_KEY=你的聊天模型服务密钥
# Also add the key variables referenced by providers you enable:
# DEEPSEEK_API_KEY=...
# OPENAI_API_KEY=...
# MOONSHOT_API_KEY=...
# GLM_API_KEY=...
# LM_STUDIO_API_KEY=lm-studio
```

所选 Provider 必须提供兼容 OpenAI Chat Completions 的 API。只需启用的服务商对应的 Key。切换 Provider 后，修改 `ACTIVE_CHAT_PROVIDER` 并重建 API、`analysis-worker` 和 `reminder-worker`（后两个服务也会读取模型配置）。LM Studio 需在本机启动服务，并使用 Docker 可访问的 `host.docker.internal` 地址。

### 2.2 Embedding 服务

Embedding 与聊天服务独立配置。单个服务商可使用以下变量：

```dotenv
EMBEDDING_PROVIDER=custom-openai-compatible
EMBEDDING_API_KEY=你的Embedding服务密钥
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

要注册多个 Embedding 服务商，可配置 JSON Provider 注册表，并选择一个服务商：

```dotenv
EMBEDDING_PROVIDER=dashscope
EMBEDDING_PROVIDERS={"dashscope":{"base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","model":"text-embedding-v3","api_key_env":"EMBEDDING_API_KEY"},"openai":{"base_url":"https://api.openai.com/v1","model":"text-embedding-3-small","api_key_env":"OPENAI_EMBEDDING_API_KEY"}}
EMBEDDING_API_KEY=当前选中Embedding服务的密钥
EMBEDDING_DIMENSIONS=1024
```

所选模型必须支持 Embeddings，且返回向量维度必须等于 `EMBEDDING_DIMENSIONS`。当前数据库向量列的维度在建表/迁移时确定；若更换为不同维度的模型，需要先为数据库向量列安排相应迁移并重新生成已有向量，不能只改 `.env`。只修改同维度的服务商或模型后，重建 API 和 Worker：

```bash
docker compose up -d --build --force-recreate api analysis-worker
```

### 2.3 百炼实时语音服务

Speech 页面使用阿里云百炼（DashScope）的 Qwen 实时语音识别和合成。需要已开通对应模型权限的百炼 API Key：

```dotenv
AI_BAILIAN_API_KEY=你的百炼语音服务密钥
QWEN_ASR_MODEL=qwen3-asr-flash-realtime
QWEN_TTS_MODEL=qwen3-tts-flash-realtime
QWEN_TTS_VOICE=Cherry
```

该 Key 用于实时 ASR/TTS，不是聊天或 Embedding 凭证。语音面试的文本回答仍由 `ACTIVE_CHAT_PROVIDER` 指定的聊天模型生成。浏览器使用 16 kHz 单声道 PCM 发送麦克风音频，后端将 Qwen 识别文本交给聊天模型，再通过 Qwen TTS 返回 24 kHz 音频片段。

麦克风需要浏览器权限。通过 `http://localhost:3000` 本机访问通常可使用浏览器的安全上下文；若改用局域网 IP 或其他非 HTTPS 地址，浏览器可能禁止麦克风访问。

配置语音后重建 API 容器：

```bash
docker compose up -d --build --force-recreate api
```

## 3. 检查 Compose 配置并启动

```bash
docker compose config --quiet
docker compose up --build -d
```

首次启动需要下载镜像并构建服务，可能需要等待。API 启动时会自动执行 Alembic 数据库迁移。

## 4. 确认服务正常

```bash
docker compose ps
docker compose logs --tail=100 api analysis-worker
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
```

存活检查应返回 `{"status":"ok"}`，就绪检查应返回 `{"status":"ready"}`。`minio-init` 是一次性初始化容器，执行完成后退出属于正常现象。

## 5. 打开应用并试用

- 前端：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- MinIO 控制台：<http://localhost:9001>

1. 注册并登录测试账号。
2. 在 **Settings** 查看服务端 `.env` 中聊天、Embedding 和语音服务的配置状态。该页面只展示状态，不输入或回显 Key。
3. 使用简历分析、文本模拟面试等聊天功能前，确认活动聊天 Provider 已配置。
4. 使用知识库前，确认 Embedding Provider 和模型维度已配置正确。
5. 测试语音面试前，在 Speech 页面点击 **Connect**、允许麦克风权限，再点击 **Enable microphone** 并开始讲话。

## 6. 修改配置与查看日志

API 与 Worker 都通过 Compose 读取 `.env`。修改 Key、模型或 Provider 选择后，需重建相关容器以载入新配置：

```bash
docker compose up -d --build --force-recreate api analysis-worker reminder-worker
```

仅修改语音设置时重建 API 即可。排查问题可查看：

```bash
docker compose logs --tail=200 api analysis-worker
```

日志中不应粘贴或分享 API Key。

## 7. 停止服务

停止容器但保留 PostgreSQL、Redis、MinIO 和上传文件数据卷：

```bash
docker compose down
```

重新启动：

```bash
docker compose up -d
```

不要使用 `docker compose down -v`，除非确定要删除所有本地数据卷。

## 8. 常见问题

### 页面能打开，但 API 或登录失败

```bash
docker compose ps
docker compose logs --tail=200 api
```

### 简历、面试或语音回答失败

确认 `ACTIVE_CHAT_PROVIDER` 对应的 API Key、模型地址和名称有效，再检查 API/Worker 日志。语音转写和合成还需要 `AI_BAILIAN_API_KEY` 以及百炼账号对所选 Qwen 实时模型的调用权限。

### 知识库文档无法变为 `ready`

确认 Embedding Provider 的 API Key、接口地址、模型名称和向量维度正确：

```bash
docker compose logs --tail=200 analysis-worker
```

### 浏览器无法启用麦克风

确认已在 Speech 页面允许麦克风访问，并通过 `localhost` 打开应用；局域网地址可能需要 HTTPS。连接语音服务前也需先配置 `AI_BAILIAN_API_KEY`。

## 9. 本地数据

Compose 使用 Docker 命名卷保存数据库、Redis、MinIO 和上传内容。普通 `docker compose down` 不会清空这些数据。本地 Compose 示例凭证仅供开发试用，不要用于生产或真实用户数据。

更多系统说明参见 [README.md](./README.md) 和 [.env.example](./.env.example)。
