"""Remove per-user encrypted model keys.

Revision ID: 0007_remove_user_model_keys
Revises: 0006_user_model_providers
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_remove_user_model_keys"
down_revision: Union[str, None] = "0006_user_model_providers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_user_model_provider_audits_user_id",
        table_name="user_model_provider_audits",
    )
    op.drop_table("user_model_provider_audits")
    op.drop_index("ix_user_model_providers_user_id", table_name="user_model_providers")
    op.drop_table("user_model_providers")


def downgrade() -> None:
    from sqlalchemy.dialects import postgresql

    op.create_table(
        "user_model_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("api_key_last4", sa.String(length=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_user_model_providers_user_id", "user_model_providers", ["user_id"]
    )
    op.create_table(
        "user_model_provider_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("purpose", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_model_provider_audits_user_id",
        "user_model_provider_audits",
        ["user_id"],
    )
