"""baseline schema adopted from SQLAlchemy metadata

This is the Alembic baseline for an application that previously created its
tables with ``Base.metadata.create_all``. It creates the same tables, so a
fresh database migrated with ``alembic upgrade head`` matches the models.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.drop_all(bind=op.get_bind())
