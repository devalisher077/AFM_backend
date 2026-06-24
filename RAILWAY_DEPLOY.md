# Railway Deploy

This backend exposes the frontend trigger endpoint:

- `GET /health`
- `GET /parse/status`
- `POST /parse/start`

Railway start command:

```bash
python api_server.py
```

Required Railway variables:

```bash
SERPAPI_KEY=
YOUTUBE_API_KEY=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
LOCAL_LLM_MODEL_PATH=models/Llama-3.2-3B-Instruct-Q4_K_M.gguf
LOCAL_LLM_CLI=llama-completion
```

Recommended variables:

```bash
CORS_ORIGIN=https://afm-ai-hack.vercel.app
ENABLE_LLM_ANALYSIS=false
ENABLE_SERPAPI_GOOGLE=true
ENABLE_YOUTUBE_API=true
ENABLE_TELEGRAM_PUBLIC_PARSE=true
ENABLE_STREAM_UPLOAD=true
AI_WORKER_ONCE=false
AI_WORKER_KEEP_RUNNING=false
LOCAL_LLM_N_CTX=8192
LOCAL_LLM_THREADS=4
LOCAL_LLM_N_BATCH=256
```

Analysis does not use external LLM APIs. `ai_worker.py` runs the local GGUF model through `llama-completion` from `llama.cpp` with CPU-only flags (`--device none`, `-ngl 0`, no KV/op offload). `ENABLE_LLM_ANALYSIS=false` only disables inline parser analysis because the streaming worker performs the analysis separately.

Local one-item analysis from Supabase and save to `ai_analyses`:

```bash
AI_WORKER_SINGLE_ITEM=true .venv/bin/python ai_worker.py
```

Dry-run one-item check from Supabase without saving:

```bash
.venv/bin/python check_local_llm_from_db.py
CHECK_URL_HASH=<url_hash> .venv/bin/python check_local_llm_from_db.py
```

After Railway deploys, copy the Railway public URL into the Vercel project:

```bash
VITE_BACKEND_URL=https://your-railway-service.up.railway.app
```

Then redeploy the Vercel frontend so the value is baked into the client bundle.
