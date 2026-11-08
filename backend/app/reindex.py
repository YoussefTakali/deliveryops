"""Re-embed every stored chunk with the currently configured EMBEDDING_PROVIDER.

Run this after changing EMBEDDING_PROVIDER (e.g. hash -> api / fastembed) so the vectors in the
database match what search() now produces for queries:

    python -m app.reindex                 # every perimeter
    python -m app.reindex <perimeter_id>  # one perimeter

On PostgreSQL, if the new model's dimension differs from EMBEDDING_DIM, update EMBEDDING_DIM and run
the alembic migration that widens the pgvector column before reindexing. SQLite stores vectors as
JSON and needs no schema change.
"""

import sys

from app import knowledge
from app.config import get_settings
from app.db import SessionLocal


def main() -> None:
    settings = get_settings()
    perimeter_id = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Reindexing (provider={settings.embedding_provider}, model="
          f"{settings.embedding_api_model if settings.embedding_provider == 'api' else settings.embedding_provider})"
          f"{f' for perimeter {perimeter_id}' if perimeter_id else ''} ...")
    with SessionLocal() as db:
        count = knowledge.reindex(db, perimeter_id)
    print(f"Done — {count} chunk(s) re-embedded.")


if __name__ == "__main__":
    main()
