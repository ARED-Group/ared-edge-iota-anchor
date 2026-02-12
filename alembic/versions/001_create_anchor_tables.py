"""Create anchor tables

Revision ID: 001
Revises: None
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("digest", sa.String(128), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="merkle_sha256"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("iota_block_id", sa.String(128), nullable=True),
        sa.Column("iota_network", sa.String(32), nullable=True),
        sa.Column("explorer_url", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("digest", "start_time", "end_time", name="uq_anchors_digest_window"),
    )

    op.create_index("idx_anchors_status", "anchors", ["status"])
    op.create_index("idx_anchors_created_at", "anchors", ["created_at"], postgresql_using="btree")

    op.create_table(
        "anchor_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("anchor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("anchors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_hash", sa.String(128), nullable=False),
        sa.Column("position_in_merkle", sa.Integer, nullable=False),
        sa.Column("merkle_proof", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index("idx_anchor_items_anchor_id", "anchor_items", ["anchor_id"])
    op.create_index("idx_anchor_items_event_hash", "anchor_items", ["event_hash"])


def downgrade() -> None:
    op.drop_table("anchor_items")
    op.drop_table("anchors")
