"""Initial retirement-core schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "household_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "plan_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("plan_id", "version_number", name="uq_plan_version_number"),
    )
    op.create_table(
        "scenario_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plan_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("engine_version", sa.String(50), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "annual_account_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenario_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.String(200), nullable=False),
        sa.Column("beginning_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("investment_return", sa.Numeric(18, 2), nullable=False),
        sa.Column("contributions", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("withdrawals", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("roth_conversions", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("qcd", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("ending_balance", sa.Numeric(18, 2), nullable=False),
        sa.UniqueConstraint("run_id", "year", "account_id", name="uq_run_year_account"),
    )


def downgrade() -> None:
    op.drop_table("annual_account_results")
    op.drop_table("scenario_runs")
    op.drop_table("plan_versions")
    op.drop_table("plans")
    op.drop_table("households")
