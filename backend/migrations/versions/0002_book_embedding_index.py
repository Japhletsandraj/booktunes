"""Add the ivfflat index on books.embedding

Kept separate from 0001 on purpose. An ivfflat index derives its centroids from
the data present when it is BUILT, so building it against an empty table gives
you garbage centroids and quietly poor recall forever after. Apply 0001, seed
books, then apply this — or rebuild afterwards with:

    python -m scripts.rebuild_vector_index

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT count(*) FROM books WHERE embedding IS NOT NULL"
    ).scalar() or 0

    # pgvector's guidance: lists ~ rows/1000 for datasets under 1M rows.
    lists = max(10, min(1000, rows // 1000)) if rows else 10
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_books_embedding ON books "
        f"USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_books_embedding")
