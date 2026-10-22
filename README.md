# TaskFlow Backend — `backend-test`

Standalone, Vercel-ready build of the TaskFlow API. It is a self-contained
FastAPI project: it has no dependency on the `backend/` folder and can be
imported into Vercel as its own project.

---

## What is different from `backend/`

| Area | `backend/` (old) | `backend-test/` (this one) |
| --- | --- | --- |
| `requirements.txt` | `aiosmtplib==3.0.3` — **does not exist on PyPI**, `pip install` fails and the Vercel build breaks | pinned to a published version (`3.0.2`) |
| Test tooling | `pytest`, `pytest-asyncio` installed in production | moved to `requirements-dev.txt` (smaller bundle, faster cold start) |
| `vercel.json` | `env` block with **placeholder** `DATABASE_URL` / `SECRET_KEY` — these override the real dashboard variables | no `env` block, so the Vercel dashboard values are used as-is |
| Routing | legacy `builds` + `routes` — the function was mounted at `/api`, so `/`, `/health` and `/docs` returned Vercel's own 404 | Vercel's FastAPI framework preset with a root `main.py` entrypoint — **every** path is routed to the app |
| Config errors | silent (`localhost` fallback) | the app always boots; the problem is reported by `/health` (503), the home endpoint and the startup log |
| Migrations | manual `alembic upgrade head` | applied automatically on cold start, guarded by a Postgres advisory lock |
| `DATABASE_URL` | must be written as `postgresql+asyncpg://` | `postgres://`, `postgresql://` and `?sslmode=require` are all normalized automatically |
| Password with `%` | breaks Alembic (`ConfigParser` interpolation) | escaped correctly |
| CORS | trailing slash in the origin never matches | trailing slashes stripped, `*.vercel.app` allowed by regex |
| Response size | uncompressed | GZip middleware |
| Logs | crash-prone on a read-only bundle | falls back to `/tmp`, never breaks the import |
| Health check | static | reports database reachability **and** migration state, returns 503 when the DB is down |

---

## 1. Fill in the credentials

Edit `backend-test/.env` and replace every `CHANGE_ME` / `your-...` value.

The two values that are **required** in production:

```ini
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DBNAME?sslmode=require
SECRET_KEY=<output of: openssl rand -hex 32>
```

The application **always boots** — it never refuses to start, because a
serverless platform that cannot import the entrypoint answers every request
with an opaque 404 and no logs at all. Instead the problem is reported:

* `GET /health` → `503` with a `configuration.errors` array naming the exact
  variable that is missing or still a placeholder,
* `GET /` → `200` with `"status": "misconfigured"` and the same list,
* the function log → one `Configuration problem: …` line per issue,
* every `/api/v1/**` route → `503` (a placeholder `SECRET_KEY` must never be
  used to sign real access tokens).

`CORS_ORIGINS` must contain the **exact origin of your frontend**, without a
trailing slash:

```ini
CORS_ORIGINS=https://frontend-five-rust-43.vercel.app,http://localhost:5173
```

### Option A — let Vercel read the file

Upload the folder (or `vercel deploy` from inside `backend-test/`) with the
edited `.env` included. The file is bundled with the function and read at
runtime.

### Option B — use the Vercel dashboard (recommended)

Vercel → **Project** → **Settings** → **Environment Variables**. Add:

| Variable | Required | Example |
| --- | --- | --- |
| `ENVIRONMENT` | yes | `production` |
| `DATABASE_URL` | **yes** | `postgresql+asyncpg://user:pw@ep-x-y.us-east-2.aws.neon.tech/taskflow?sslmode=require` |
| `SECRET_KEY` | **yes** | `openssl rand -hex 32` |
| `CORS_ORIGINS` | **yes** | `https://frontend-five-rust-43.vercel.app` |
| `APP_PREFIX` | no | `/api/v1` |
| `LOG_LEVEL` | no | `INFO` |
| `DOCS_ENABLED` | no | `true` (the docs are served by default — set `false` to hide `/docs`) |
| `AUTO_MIGRATE` | no | `true` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | no | `2` / `2` |
| `DB_SSL` | no | `auto` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | no | Gmail App Password for OTP e-mails |
| `OTP_EXPIRE_MINUTES` | no | `10` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | no | Google OAuth |
| `OPENROUTER_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` | no | AI assistant (tried in that order) |

> Environment variables always win over the values in `.env`, so Option B
> safely overrides Option A.

> `vercel.json` deliberately contains **no `env` block**. A value defined
> there overrides the dashboard, which is exactly how the old deployment ended
> up running against a placeholder database.

---

## 2. Create the Vercel project

1. **Add New → Project** and import this repository.
2. **Root Directory** → `backend-test`.
3. Framework preset → auto-detected **FastAPI**.
4. Add the environment variables above (Option B).
5. **Deploy.**

Vercel reads `.python-version` (3.12) and `pyproject.toml`
(`tool.vercel.entrypoint = "main:app"`), so the correct entrypoint is
picked without any guessing.

> **Why the entrypoint lives in the project root.**
> Vercel resolves a Python entrypoint from `app.py` / `index.py` /
> `server.py` / `main.py` / `wsgi.py` / `asgi.py` at the project root (or
> inside `src/` / `app/`). When the entrypoint sits at the root, Vercel
> routes **every** request to the function — that is what makes `/`,
> `/health` and `/docs` reachable.
>
> The older layout (`api/index.py` plus `builds`/`routes`) mounts the
> function under `/api` only, and every other path is answered by Vercel's
> edge with a plain-text "Page Not Found". That is exactly the symptom of
> the old deployment.

---

## 3. Point the frontend at the new backend

Set this in the **frontend** project (see `frontend-test/README.md`):

```ini
API_BASE_URL=https://<your-new-backend>.vercel.app/api/v1
```

The backend serves the API under `/api/v1` (`APP_PREFIX`) and exposes
`/health` for monitoring.

---

## 4. Local development

```bash
cd backend-test
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env               # then edit it
uvicorn app.main:app --reload --port 8000
```

* API: <http://localhost:8000/api/v1>
* Swagger UI: <http://localhost:8000/docs> (served by default — set
  `DOCS_ENABLED=false` to hide it)
* Health: <http://localhost:8000/health>

Run the tests (needs a PostgreSQL test database in `TEST_DATABASE_URL`):

```bash
pytest -q
```

Run the migrations by hand:

```bash
alembic upgrade head
```

---

## 5. Verify the deployment

After the first deploy, check these four URLs. All of them must answer with
JSON (a plain-text "Page Not Found" means the function was never mounted):

| URL | Expected |
| --- | --- |
| `https://<your-backend>.vercel.app/` | `200` — `{"message": "TaskFlow API is running", …}` |
| `https://<your-backend>.vercel.app/health` | `200` — `{"status": "healthy", "database": "connected"}` (or `503` with the reason) |
| `https://<your-backend>.vercel.app/docs` | `200` — the Swagger UI page |
| `https://<your-backend>.vercel.app/api/v1/health` | `200` — the same check under the API prefix |

If `/health` answers `503`, the JSON body names the missing variable under
`configuration.errors` — set it in the Vercel dashboard and redeploy.

---

## Project layout

```
backend-test/
├── main.py                # Vercel entrypoint at the project root (exposes `app`)
├── app/
│   ├── config.py         # settings, URL normalization, config diagnostics
│   ├── database.py       # async engine, session factory, get_db
│   ├── main.py           # FastAPI app, CORS, GZip, lifespan, /health
│   ├── migrations.py     # auto-migrate on cold start (advisory lock)
│   ├── models/           # SQLAlchemy ORM models
│   ├── routers/          # HTTP endpoints
│   ├── schemas/          # Pydantic request/response models
│   └── services/         # business logic
├── alembic/              # versioned migrations (run automatically)
├── tests/                # pytest suite (not deployed)
├── .env                  # your credentials (git-ignored)
├── .env.example          # safe template
├── pyproject.toml        # deps + Vercel entrypoint
├── requirements.txt      # runtime dependencies
├── requirements-dev.txt  # runtime + test dependencies
└── vercel.json           # function config
```

---

## Notes

* **Migrations run on cold start.** The first request of a cold function
  applies any pending Alembic migration. A PostgreSQL advisory lock makes sure
  two warm instances never migrate at the same time, and a `create_all`
  fallback covers the case where the `alembic/` folder is missing from the
  bundle.
* **Connection pool.** The defaults (`DB_POOL_SIZE=2`, `DB_MAX_OVERFLOW=2`,
  `DB_POOL_RECYCLE=280`) keep the number of open connections small, which
  matters on free-tier databases that cap concurrent connections. Raise them
  only if you also raise the database limit.
* **Region.** If your database is in a specific region, set the function region
  in the Vercel dashboard to match — it removes most of the latency.
* **`logs/`** is not used in production: the assistant logger falls back to
  `/tmp` on the read-only Vercel bundle and always mirrors to stdout, so the
  output is visible in the Vercel function logs.
* **Python version.** Use 3.12 — the system `python3` on some machines is
  3.14, for which the pinned wheels (asyncpg, pydantic-core) are not built.
