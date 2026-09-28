"""Initial SupportNova schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-24
"""

from alembic import op

# Tables are created by SQLAlchemy metadata on application startup.
# Generate a full autogenerate revision after the database is reachable:
#   alembic revision --autogenerate -m "sync schema"

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
