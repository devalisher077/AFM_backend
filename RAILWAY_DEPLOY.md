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
OPENAI_API_KEY=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Recommended variables:

```bash
CORS_ORIGIN=https://afm-ai-hack.vercel.app
ENABLE_OPENAI_ANALYSIS=false
ENABLE_SERPAPI_GOOGLE=true
ENABLE_YOUTUBE_API=true
ENABLE_TELEGRAM_PUBLIC_PARSE=true
ENABLE_STREAM_UPLOAD=true
AI_WORKER_ONCE=false
AI_WORKER_KEEP_RUNNING=false
```

After Railway deploys, copy the Railway public URL into the Vercel project:

```bash
VITE_BACKEND_URL=https://your-railway-service.up.railway.app
```

Then redeploy the Vercel frontend so the value is baked into the client bundle.
