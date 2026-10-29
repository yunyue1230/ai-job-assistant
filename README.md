# AI Interview Guide (Python)

![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)

This repository is evolving from a Streamlit resume/JD matching demo into a
Python interview-preparation platform. The existing Streamlit app remains
available; the FastAPI backend is being added alongside it.

## Implemented backend flow

1. Register and authenticate a user with a bearer token.
2. Upload a TXT, Markdown, or text-based PDF resume with a job description.
3. Store the file through the configured storage backend and save metadata in
   PostgreSQL.
4. Enqueue an analysis job in Redis Streams.
5. Run the independent worker to parse the resume and reuse the existing
   structured job-match analysis.
6. Poll analysis status, browse history, or retry failed jobs.

The backend foundation, resume-analysis workflow, knowledge-base/RAG workflow,
text mock interviews, interview scheduling/reminders, independently configured
chat and embedding providers, a DashScope realtime voice interview, and a React
frontend are implemented.

## Local development

Use the project's Conda environment for all Python commands:

```bash
conda activate ai-job-assistant
cp -n .env.example .env
```

Set the key for your selected chat provider in `.env`. Configure embeddings
independently with `EMBEDDING_API_KEY` or `EMBEDDING_PROVIDERS`, and set
`AI_BAILIAN_API_KEY` for realtime speech. Do not commit `.env`; the website
does not accept or store user API keys.

Start the React frontend, PostgreSQL, Redis, MinIO, FastAPI backend, analysis
worker, and reminder worker:

```bash
docker compose up -d
```

Open the application at `http://localhost:3000`. The Vite development server is
also available with `cd frontend && npm run dev`, which proxies API requests to
`http://localhost:8000`.

For local development without Dockerized API/workers, run migrations and start
the API:

```bash
conda run -n ai-job-assistant alembic upgrade head
conda run -n ai-job-assistant uvicorn backend.app.main:app --reload
```

API documentation is available at `http://localhost:8000/docs`.
The reminder worker can be run separately with
`conda run -n ai-job-assistant python -m backend.app.workers.reminders`.

## Storage backends

### Local filesystem

This is the default:

```dotenv
STORAGE_BACKEND=local
UPLOAD_DIR=uploads
```

Uploaded files are ignored by Git.

### Local MinIO

Start the Compose services, which create the `interview-guide` bucket, then set:

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=interview-guide
S3_ENDPOINT_URL=http://localhost:9000
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=local-minio
S3_SECRET_ACCESS_KEY=local-minio-password
```

The MinIO console is available at `http://localhost:9001`. The sample
credentials in Compose are for local development only.

### AWS S3

Set `STORAGE_BACKEND=s3` and `S3_BUCKET`. Leave `S3_ENDPOINT_URL` unset and use
the AWS credential chain (prefer an IAM role or other temporary credentials).
The same storage interface is used for local files, MinIO, and AWS S3; a single
application process selects one backend at startup.

## API outline

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/token` (OAuth2 password form: `username`, `password`)
- `GET /api/v1/auth/me`
- `POST /api/v1/resumes` (multipart form: `file`, `job_description`)
- `GET /api/v1/resumes`
- `GET /api/v1/analyses`
- `GET /api/v1/analyses/{job_id}`
- `GET /api/v1/analyses/{job_id}/report`
- `POST /api/v1/analyses/{job_id}/retry`
- `POST /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases`
- `POST /api/v1/knowledge-bases/{id}/documents`
- `GET /api/v1/knowledge-bases/{id}/documents`
- `POST /api/v1/knowledge-bases/{id}/documents/{document_id}/retry`
- `POST /api/v1/knowledge-bases/{id}/chat`
- `GET /api/v1/knowledge-bases/{id}/documents/{document_id}/download`
- `POST /api/v1/interviews`
- `GET /api/v1/interviews`
- `GET /api/v1/interviews/{interview_id}`
- `POST /api/v1/interviews/{interview_id}/answers`
- `POST /api/v1/interviews/{interview_id}/finish`
- `GET /api/v1/interviews/{interview_id}/report`
- `POST /api/v1/schedule/events`
- `GET /api/v1/schedule/events`
- `PATCH /api/v1/schedule/events/{event_id}`
- `DELETE /api/v1/schedule/events/{event_id}`
- `GET /api/v1/schedule/events.ics`
- `GET /api/v1/notifications`
- `POST /api/v1/notifications/{notification_id}/read`
- `GET /api/v1/settings/models`
- `WS /api/v1/speech/realtime` (authenticated WebSocket voice interview)
- `GET /health/live`
- `GET /health/ready`

Resume and analysis endpoints require `Authorization: Bearer <token>`.
Knowledge-base endpoints use the same bearer-token authentication. Documents
are parsed and embedded by the worker; their status is `queued`, `processing`,
`ready`, or `failed`. Chat answers are grounded in the top matching chunks and
return source labels, filenames, optional page numbers, and excerpts.

The application assumes the configured embedding model returns the configured
vector dimension (1536 by default, at most 2000 for the configured HNSW index).
The pgvector extension and cosine HNSW index are enabled by the Alembic
migration on PostgreSQL. SQLite remains useful for local unit tests but does
not support pgvector similarity search. Offline retrieval evaluation helpers
for recall@k and reciprocal rank are available in `backend/app/ai/evaluation.py`.

## Text mock interviews

The interview API creates a personalized first question from the role, job
description, resume, and difficulty. Each submitted answer is evaluated with a
score, strengths, and improvement suggestions; the interviewer either asks a
follow-up or generates the next question. Sessions can be finished early after
at least one answer, or automatically complete at the configured question
count, producing an overall assessment and a downloadable PDF report. An
optional owned knowledge base adds retrieved reference context to the questions
and answer evaluation.

All interview endpoints use the bearer-token authentication. The create
request accepts `role`, `job_description`, `resume_text`, `difficulty`
(`beginner`, `intermediate`, or `advanced`), `max_questions` (1-20), and an
optional `knowledge_base_id`.

## Scheduling, model providers, and speech

Schedule events require timezone-aware timestamps and an IANA `timezone_name`.
The reminder worker creates in-app notifications when an event reaches its
configured reminder window; clients poll `/api/v1/notifications`. Events can be
exported as an `.ics` calendar file.

Chat-model providers use `MODEL_PROVIDERS` JSON and `ACTIVE_CHAT_PROVIDER`.
Each chat provider specifies a base URL, model name, and the name of an
environment variable holding its API key. The API only exposes provider names, models,
base URLs, active status, and whether a key is configured; it never returns key
values. For example:

```dotenv
ACTIVE_CHAT_PROVIDER=deepseek
MODEL_PROVIDERS={"deepseek":{"base_url":"https://api.deepseek.com","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY"},"openai":{"base_url":"https://api.openai.com/v1","model":"gpt-4o-mini","api_key_env":"OPENAI_API_KEY"}}
```

Provider API keys are read from local environment variables only. Users cannot
enter or retrieve keys in Settings. Embeddings use an independent
`EMBEDDING_PROVIDER` selection and `EMBEDDING_PROVIDERS` registry (or the
single-provider `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, and
`EMBEDDING_MODEL` settings). The selected embedding model must return exactly
`EMBEDDING_DIMENSIONS`; changing dimensions also requires a matching database
vector-column migration/rebuild.

Realtime speech uses `AI_BAILIAN_API_KEY` for DashScope Qwen realtime ASR
(`QWEN_ASR_MODEL`) and TTS (`QWEN_TTS_MODEL`, `QWEN_TTS_VOICE`). The browser
streams 16 kHz mono PCM to the authenticated backend WebSocket; the backend
transcribes it, calls the configured active chat provider for an interviewer
reply, and streams 24 kHz PCM speech back. This is a local-Docker setup; the
DashScope account must have access to the configured realtime models.

The React client has sign-in/registration and working screens for resume
analysis, knowledge-base uploads and grounded Q&A, text interviews, schedule
management, realtime voice interviews, and model provider status. It is built
as a static Vite bundle and served through Nginx in Compose.

## Tests

```bash
conda run -n ai-job-assistant python -m unittest discover -s tests -v
```

Build the frontend with Node.js:

```bash
npm ci --prefix frontend
npm --prefix frontend run build
```
