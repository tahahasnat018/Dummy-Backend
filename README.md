# Dummy Backend

FastAPI starter for local development.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Database

1) Copy `.env.example` to `.env` and update `DATABASE_URL`.
2) Create the database in Postgres.
3) Create the first migration and apply it:

```bash
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## Run

```bash
uvicorn app.main:app --reload
```

## Endpoints

- `GET /` -> basic status
- `GET /health` -> health check
