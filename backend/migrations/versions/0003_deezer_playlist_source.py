"""Allow 'deezer' as a playlist source

Spotify stopped being usable as the primary track source: apps created after
2024-11-27 get 403 from /recommendations, and search now requires the app owner
to hold an active Premium subscription. Discovery moved to Deezer's public API,
which needs no key of any kind, with YouTube Music resolving each name to a
playable track.

A playlist built that way is stored with source='deezer' (that's what curated
it) while its individual tracks carry source='youtube_music' (that's what plays
them). 'spotify' stays permitted — the code path is retained and re-enables
itself if credentials are ever configured, and existing rows must stay valid.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_playlists_source"
_TABLE = "book_playlists"

_OLD = "source IN ('spotify', 'youtube_music', 'custom')"
_NEW = "source IN ('deezer', 'spotify', 'youtube_music', 'custom')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW)


def downgrade() -> None:
    # Rows added since the upgrade would violate the narrower constraint, so
    # retire them first rather than letting create_check_constraint fail.
    op.execute(f"DELETE FROM {_TABLE} WHERE source = 'deezer'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD)
