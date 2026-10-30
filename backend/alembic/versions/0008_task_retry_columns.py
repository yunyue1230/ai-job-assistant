"""Add automatic retry bookkeeping to queued tasks.

Revision ID: 0008_task_retry_columns
Revises: 0007_remove_user_model_keys

``analysis_jobs`` already counted attempts; it gains the ceiling those attempts
are compared with. ``knowledge_documents`` gains both, so ingestion can retry a
transient embedding failure instead of failing the document outright.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_task_retry_columns"
down_revision: Union[str, None] = "0007_remove_user_model_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "max_attempts")
    op.drop_column("knowledge_documents", "attempts")
    op.drop_column("analysis_jobs", "max_attempts")
