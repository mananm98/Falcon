import aiosqlite
from pathlib import Path

from app.config import settings

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "db" / "migrations"


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def run_migrations() -> None:
    db = await get_db()
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
        )
        async with db.execute("SELECT name FROM _migrations") as cursor:
            applied = {row[0] for row in await cursor.fetchall()}

        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        for migration_file in migration_files:
            if migration_file.name not in applied:
                sql = migration_file.read_text()
                await db.executescript(sql)
                await db.execute(
                    "INSERT INTO _migrations (name) VALUES (?)", (migration_file.name,)
                )
                await db.commit()
    finally:
        await db.close()
