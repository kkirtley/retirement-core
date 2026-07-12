import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from retirement_core.infrastructure.db.base import Base


class HouseholdModel(Base):
    __tablename__ = "households"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    plans: Mapped[list["PlanModel"]] = relationship(back_populates="household")


class PlanModel(Base):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    household: Mapped[HouseholdModel] = relationship(back_populates="plans")
    versions: Mapped[list["PlanVersionModel"]] = relationship(back_populates="plan")


class PlanVersionModel(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version_number"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    plan: Mapped[PlanModel] = relationship(back_populates="versions")
    runs: Mapped[list["ScenarioRunModel"]] = relationship(back_populates="plan_version")


class ScenarioRunModel(Base):
    __tablename__ = "scenario_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_version: Mapped[PlanVersionModel] = relationship(back_populates="runs")
    annual_accounts: Mapped[list["AnnualAccountResultModel"]] = relationship(back_populates="run")


class AnnualAccountResultModel(Base):
    __tablename__ = "annual_account_results"
    __table_args__ = (UniqueConstraint("run_id", "year", "account_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenario_runs.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    beginning_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    investment_return: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    contributions: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    withdrawals: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    roth_conversions: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    qcd: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    ending_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    run: Mapped[ScenarioRunModel] = relationship(back_populates="annual_accounts")
