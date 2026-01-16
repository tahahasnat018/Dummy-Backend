from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    Index,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ACTIVE")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class Location(Base):
    __tablename__ = "location"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    currency_code: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    asset_name: Mapped[str] = mapped_column(Text, nullable=False)
    serial_number: Mapped[str | None] = mapped_column(Text)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BusinessDay(Base):
    __tablename__ = "business_day"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_date: Mapped[Date] = mapped_column(Date, nullable=False)
    starts_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_open_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    planned_close_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    actual_open_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    actual_close_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    late_open: Mapped[bool | None] = mapped_column(Boolean)
    early_close: Mapped[bool | None] = mapped_column(Boolean)


class CashCount(Base):
    __tablename__ = "cash_count"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    counted_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_cash: Mapped[Numeric | None] = mapped_column(Numeric)
    counted_cash: Mapped[Numeric | None] = mapped_column(Numeric)
    variance: Mapped[Numeric | None] = mapped_column(Numeric)
    cashier_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class ChannelMapping(Base):
    __tablename__ = "channel_mapping"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(Text)
    source_channel_code: Mapped[str] = mapped_column(Text, nullable=False)
    source_channel_name: Mapped[str | None] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text, nullable=False)


class Employee(Base):
    __tablename__ = "employee"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("location.id")
    )
    external_key: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ExternalIdMap(Base):
    __tablename__ = "external_id_map"

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), primary_key=True
    )
    source_system_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_system.id"), primary_key=True
    )
    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    internal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedIngestLog(Base):
    __tablename__ = "feed_ingest_log"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    source_system_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_system.id"), nullable=False
    )
    feed_type: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class LedgerEvent(Base):
    __tablename__ = "ledger_event"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    feed_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    ingested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class Incident(Base):
    __tablename__ = "incident"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    asset_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("asset.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN")
    incident_type: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class InventoryCount(Base):
    __tablename__ = "inventory_count"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False
    )
    counted_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    on_hand_qty: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    uom: Mapped[str] = mapped_column(Text, nullable=False)
    count_type: Mapped[str] = mapped_column(Text, nullable=False, default="SNAPSHOT")
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class InventoryMovement(Base):
    __tablename__ = "inventory_movement"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False
    )
    occurred_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    uom: Mapped[str] = mapped_column(Text, nullable=False)
    unit_cost: Mapped[Numeric | None] = mapped_column(Numeric)
    currency_code: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class IngestionEventMap(Base):
    __tablename__ = "ingestion_event_map"
    __table_args__ = (
        Index(
            "ix_ingestion_event_map_unique",
            "tenant_id",
            "source_system_id",
            "source_event_id",
            "entity_type",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    source_system_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_system.id"), nullable=False
    )
    source_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemCategory(Base):
    __tablename__ = "item_category"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("item_category.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("item_category.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    base_uom: Mapped[str] = mapped_column(Text, nullable=False)
    sellable_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipe_id: Mapped[int | None] = mapped_column(BigInteger)
    bom_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemCost(Base):
    __tablename__ = "item_cost"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False
    )
    location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("location.id")
    )
    cost_per_base_uom: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    currency_code: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Date | None] = mapped_column(Date)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )


class ItemExternalKey(Base):
    __tablename__ = "item_external_key"

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), primary_key=True
    )
    source_system_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_system.id"), primary_key=True
    )
    external_item_key: Mapped[str] = mapped_column(Text, primary_key=True)


class LaborCostRate(Base):
    __tablename__ = "labor_cost_rate"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee.id")
    )
    role: Mapped[str | None] = mapped_column(Text)
    hourly_rate: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    currency_code: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Date | None] = mapped_column(Date)
    payroll_code: Mapped[str | None] = mapped_column(Text)


class LaborPunch(Base):
    __tablename__ = "labor_punch"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employee.id"), nullable=False
    )
    clock_in: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_out: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class LocationHours(Base):
    __tablename__ = "location_hours"
    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="day_of_week"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(nullable=False)
    open_local: Mapped[Time] = mapped_column(Time, nullable=False)
    close_local: Mapped[Time] = mapped_column(Time, nullable=False)
    business_day_cutover_local: Mapped[Time] = mapped_column(
        Time, nullable=False, default="05:00:00"
    )
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LocationHoursException(Base):
    __tablename__ = "location_hours_exception"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    date_local: Mapped[Date] = mapped_column(Date, nullable=False)
    open_local: Mapped[Time | None] = mapped_column(Time)
    close_local: Mapped[Time | None] = mapped_column(Time)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)


class OpenCloseSignal(Base):
    __tablename__ = "open_close_signal"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class Policy(Base):
    __tablename__ = "policy"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Date] = mapped_column(Date, nullable=False)
    config: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class Payout(Base):
    __tablename__ = "payout"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("location.id")
    )
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    provider: Mapped[str | None] = mapped_column(Text)
    payout_reference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    currency_code: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    expected_payout_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class PosDowntimeEvent(Base):
    __tablename__ = "pos_downtime_event"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class PosTerminal(Base):
    __tablename__ = "pos_terminal"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    terminal_code: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Recipe(Base):
    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    output_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False
    )
    output_qty: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=1)
    output_uom: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RecipeComponent(Base):
    __tablename__ = "recipe_component"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    recipe_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("recipe.id"), nullable=False
    )
    component_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False
    )
    qty: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    uom: Mapped[str] = mapped_column(Text, nullable=False)


class ReportRun(Base):
    __tablename__ = "report_run_state"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("business_day.id"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("snapshot.id"), nullable=False
    )
    report_version: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    numbers_status: Mapped[str] = mapped_column(Text, nullable=False)
    feed_completeness_flags: Mapped[dict | None] = mapped_column(JSON_TYPE)
    truth_labels: Mapped[dict | None] = mapped_column(JSON_TYPE)
    kpis: Mapped[dict | None] = mapped_column(JSON_TYPE)
    alerts: Mapped[dict | None] = mapped_column(JSON_TYPE)
    ops_issues: Mapped[dict | None] = mapped_column(JSON_TYPE)
    recommendations: Mapped[dict | None] = mapped_column(JSON_TYPE)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class SourceSystem(Base):
    __tablename__ = "source_system"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    system_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Snapshot(Base):
    __tablename__ = "snapshot"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("business_day.id"), nullable=False
    )
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary_hash: Mapped[str] = mapped_column(Text, nullable=False)
    day_state_object: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    snapshot_metadata: Mapped[dict | None] = mapped_column(JSON_TYPE)


class StockoutEvent(Base):
    __tablename__ = "stockout_event"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("item.id"))
    item_name_raw: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class Ticket(Base):
    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    business_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_day.id")
    )
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_system.id")
    )
    external_ticket_id: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("channel_mapping.id")
    )
    order_type_raw: Mapped[str | None] = mapped_column(Text)
    covers: Mapped[int | None] = mapped_column()
    gross_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=0)
    discount_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=0)
    net_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=0)
    tax_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=0)
    cashier_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee.id")
    )
    terminal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pos_terminal.id")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="CLOSED")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class TicketDiscount(Base):
    __tablename__ = "ticket_discount"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), nullable=False
    )
    amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class TicketLineItem(Base):
    __tablename__ = "ticket_line_item"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("item.id"))
    item_name_raw: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    uom: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Numeric | None] = mapped_column(Numeric)
    gross_line_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    discount_line_amount: Mapped[Numeric] = mapped_column(
        Numeric, nullable=False, default=0
    )
    net_line_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    tax_line_amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False, default=0)
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("channel_mapping.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class TicketPayment(Base):
    __tablename__ = "ticket_payment"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), nullable=False
    )
    paid_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    tender_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    card_brand: Mapped[str | None] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class TicketRefund(Base):
    __tablename__ = "ticket_refund"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), nullable=False
    )
    amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    refunded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    payment_method: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class TicketVoid(Base):
    __tablename__ = "ticket_void"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket.id"), nullable=False
    )
    amount: Mapped[Numeric] = mapped_column(Numeric, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    cashier_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee.id")
    )
    terminal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pos_terminal.id")
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)


class UomConversion(Base):
    __tablename__ = "uom_conversion"
    __table_args__ = (
        CheckConstraint("scope IN ('GLOBAL', 'ITEM')", name="uom_scope"),
        CheckConstraint("factor > 0", name="uom_factor_positive"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("item.id"))
    from_uom: Mapped[str] = mapped_column(Text, nullable=False)
    to_uom: Mapped[str] = mapped_column(Text, nullable=False)
    factor: Mapped[Numeric] = mapped_column(Numeric, nullable=False)


class WorkOrder(Base):
    __tablename__ = "work_order"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant.id"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("location.id"), nullable=False
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id")
    )
    asset_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("asset.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_TYPE)
