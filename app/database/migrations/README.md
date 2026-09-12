# Migrations

On startup, `init_db()` (see `app/database/database.py`) runs
`Base.metadata.create_all`, which creates any missing tables. This is safe to
run every boot and never touches existing data or columns.

For production deployments where you need to evolve the schema over time
without losing data (adding/renaming columns, etc.), wire in Alembic:

```bash
pip install alembic
alembic init app/database/migrations
# edit alembic.ini's sqlalchemy.url to read from DATABASE_URL
# edit migrations/env.py: target_metadata = Base.metadata (import from app.database.models)
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

For the default SQLite/dev setup and most small-to-medium PostgreSQL
deployments, `create_all` on startup is sufficient and is what this project
uses out of the box.
