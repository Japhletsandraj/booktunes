"""Report what is actually in a database.

Written to answer one specific question: `/health` reports `database: true`
while every real request returns 503. That combination means the *connection*
works but the *schema* is missing — `check_database()` only runs `SELECT 1`
and a pgvector probe, neither of which touches a table.

Usage — against whatever `.env` points at:

    python -m scripts.check_db

Against a deployed database, override the URL for one command:

    # PowerShell
    $env:DATABASE_URL="Add_url_here"; python -m scripts.check_db

    # bash
    DATABASE_URL="Add_url_here" python -m scripts.check_db

Take the URL from the Render dashboard (service -> Environment -> DATABASE_URL).
Render's *internal* hostname only resolves inside Render, so from your own
machine use the External Database URL instead.
"""

import asyncio
import sys

from sqlalchemy import func, inspect, select, text

from app.core.database import dispose_engine, get_engine, get_sessionmaker
from app.models.models import Book, BookPlaylist, User

# Tables worth counting, in the order a reader cares about them.
COUNTED = [("books", Book), ("users", User), ("playlists", BookPlaylist)]


async def main() -> int:
    engine = get_engine()

    # Mask the password: this output tends to get pasted into chats and issues.
    print(f"target : {engine.url.render_as_string(hide_password=True)}")

    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: sorted(inspect(c).get_table_names()))
            version = (await conn.execute(text("SELECT version()"))).scalar()
            has_vector = (
                await conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar() is not None
    except Exception as exc:
        print(f"\nCANNOT CONNECT: {type(exc).__name__}: {exc}")
        print("\nCheck the host, port and credentials in DATABASE_URL.")
        await dispose_engine()
        return 2

    server = (version or "unknown").split(" on ")[0]
    print(f"server : {server}")
    print(f"pgvector: {'installed' if has_vector else 'MISSING'}")
    listing = " -> " + ", ".join(tables) if tables else ""
    print(f"tables : {len(tables)}{listing}")

    if not tables:
        print(
            "\nThe connection works but the database is EMPTY — no schema has been\n"
            "created. That is exactly what makes /health report database:true while\n"
            "every query returns 503 database_error.\n"
            "\nFix it by running the migrations against this same URL:\n"
            "    alembic upgrade head"
        )
        await dispose_engine()
        return 1

    if not has_vector:
        print("\nWARNING: pgvector absent. Run: CREATE EXTENSION IF NOT EXISTS vector;")

    absent = [name for name, model in COUNTED if model.__tablename__ not in tables]
    if absent:
        print(f"\nWARNING: expected tables missing: {', '.join(absent)}")
        print("Migrations are only partly applied. Run: alembic upgrade head")

    print()
    async with get_sessionmaker()() as session:
        for label, model in COUNTED:
            if model.__tablename__ not in tables:
                continue
            count = await session.scalar(select(func.count()).select_from(model))
            print(f"{label:<10} {count}")

        revision = None
        if "alembic_version" in tables:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        print(f"{'migration':<10} {revision or 'unknown'}")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
