from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    Asset,
    BusinessDay,
    CashCount,
    ChannelMapping,
    Employee,
    ExternalIdMap,
    LedgerEvent,
    Incident,
    IngestionEventMap,
    Item,
    ItemCategory,
    ItemCost,
    ItemExternalKey,
    LaborCostRate,
    LaborPunch,
    Location,
    LocationHours,
    LocationHoursException,
    OpenCloseSignal,
    Payout,
    Policy,
    PosDowntimeEvent,
    PosTerminal,
    Recipe,
    RecipeComponent,
    ReportRun,
    Snapshot,
    SourceSystem,
    StockoutEvent,
    Tenant,
    Ticket,
    TicketDiscount,
    TicketLineItem,
    TicketPayment,
    TicketRefund,
    TicketVoid,
    UomConversion,
    WorkOrder,
)

app = FastAPI(title="Dummy Backend")


class Meta(BaseModel):
    request_id: str
    warnings: list[str]


class Envelope(BaseModel):
    data: Any
    meta: Meta


def _meta(request_id: Optional[str] = None, warnings: Optional[list[str]] = None) -> dict:
    return {
        "request_id": request_id or f"req_{uuid4().hex}",
        "warnings": warnings or [],
    }


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _paginate_by_id(query, model, limit: int, cursor: Optional[int]) -> tuple[list[Any], Optional[int]]:
    if cursor is not None:
        query = query.filter(model.id > cursor)
    rows = query.order_by(model.id).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        next_cursor = rows[limit - 1].id
        rows = rows[:limit]
    return rows, next_cursor


def _paginate_by_offset(query, limit: int, cursor: Optional[int]) -> tuple[list[Any], Optional[int]]:
    offset = cursor or 0
    rows = query.offset(offset).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        next_cursor = offset + limit
        rows = rows[:limit]
    return rows, next_cursor


def _list_meta(limit: int, cursor: Optional[int], next_cursor: Optional[int]) -> dict:
    meta = _meta()
    if next_cursor is not None:
        meta["page"] = {"limit": limit, "cursor": str(next_cursor)}
    elif cursor is not None:
        meta["page"] = {"limit": limit, "cursor": str(cursor)}
    else:
        meta["page"] = {"limit": limit, "cursor": None}
    return meta


ERP_LEDGER_FEED_TYPES = {
    "sales": "ERP_SALES_LEDGER",
    "orders-tickets": "ERP_ORDERS_TICKETS_LEDGER",
    "discounts": "ERP_DISCOUNTS_LEDGER",
    "voids": "ERP_VOIDS_LEDGER",
    "refunds": "ERP_REFUNDS_LEDGER",
    "open-close-downtime": "ERP_OPEN_CLOSE_DOWNTIME_LEDGER",
    "payments-tender": "ERP_PAYMENTS_TENDER_LEDGER",
    "payouts": "ERP_PAYOUT_LEDGER",
    "cash-variance": "ERP_CASH_VARIANCE_LEDGER",
    "labor": "ERP_LABOR_LEDGER",
    "stockout-86": "ERP_STOCKOUT_86_LEDGER",
    "costing-inputs": "ERP_COSTING_INPUTS_LEDGER",
    "ops-incidents": "ERP_OPS_INCIDENTS_LEDGER",
}


def _ledger_event_time(payload: dict) -> datetime:
    if "event_time" in payload and payload["event_time"] is not None:
        return payload["event_time"]
    for key in (
        "closed_at",
        "opened_at",
        "paid_at",
        "applied_at",
        "voided_at",
        "refunded_at",
        "occurred_at",
    ):
        if key in payload and payload[key] is not None:
            return payload[key]
    return _now()


def _append_ledger_events(
    db: Session,
    tenant_id: int,
    location_id: Optional[int],
    feed_type: str,
    domain: str,
    source_system_id: int,
    business_day_id: Optional[int],
    events: list[dict],
) -> dict:
    inserted = 0
    deduped = 0
    errors: list[str] = []
    for event in events:
        source_event_id = event.get("source_event_id")
        if not source_event_id:
            errors.append("missing_source_event_id")
            continue
        exists = db.query(LedgerEvent).filter(
            LedgerEvent.tenant_id == tenant_id,
            LedgerEvent.location_id == location_id,
            LedgerEvent.feed_type == feed_type,
            LedgerEvent.domain == domain,
            LedgerEvent.source_event_id == source_event_id,
        ).first()
        if exists:
            deduped += 1
            continue
        event_time = _ledger_event_time(event)
        row = LedgerEvent(
            tenant_id=tenant_id,
            location_id=location_id,
            business_day_id=business_day_id,
            source_system_id=source_system_id,
            domain=domain,
            feed_type=feed_type,
            source_event_id=source_event_id,
            occurred_at=event_time,
            payload=event,
            ingested_at=_now(),
        )
        db.add(row)
        inserted += 1
    db.commit()
    return {
        "accepted": True,
        "ledger": feed_type,
        "inserted": inserted,
        "deduped": deduped,
        "errors": errors,
    }


def _business_day_bounds(target_date: date, tz: ZoneInfo, cutover: time) -> tuple[datetime, datetime]:
    starts_at = datetime.combine(target_date, cutover, tzinfo=tz)
    ends_at = starts_at + timedelta(days=1) - timedelta(seconds=1)
    return starts_at, ends_at


@app.get("/", tags=["root"])
def read_root() -> dict:
    return {"status": "ok"}


@app.get("/health", tags=["health"])
def health_check() -> dict:
    return {"status": "healthy"}


class SourceSystemCreate(BaseModel):
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "name": "pos_square",
                "type": "POS",
                "is_active": True,
                "tenant_id": 1,
            }
        },
    }
    name: str
    type: str = Field(alias="type")
    is_active: bool = True
    tenant_id: int = 1


class SourceSystemList(BaseModel):
    is_active: Optional[bool] = None


@app.post("/api/v1/source-systems", tags=["Source Systems"])
def create_source_system(payload: SourceSystemCreate, db: Session = Depends(get_db)) -> dict:
    source_system = SourceSystem(
        tenant_id=payload.tenant_id,
        system_type=payload.type,
        provider=payload.name,
        name=payload.name,
        is_active=payload.is_active,
    )
    db.add(source_system)
    db.commit()
    db.refresh(source_system)
    return {
        "data": {
            "source_system_id": source_system.id,
            "name": source_system.name,
            "type": source_system.system_type,
            "is_active": source_system.is_active,
            "created_at": _now().isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/source-systems/{source_system_id}", tags=["Source Systems"])
def get_source_system(source_system_id: int, db: Session = Depends(get_db)) -> dict:
    system = db.get(SourceSystem, source_system_id)
    if not system:
        raise HTTPException(status_code=404, detail="source system not found")
    return {
        "data": {
            "source_system_id": system.id,
            "tenant_id": system.tenant_id,
            "system_type": system.system_type,
            "provider": system.provider,
            "name": system.name,
            "is_active": system.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/source-systems", tags=["Source Systems"])
def list_source_systems(
    tenant_id: Optional[int] = Query(default=None),
    system_type: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(SourceSystem)
    if tenant_id is not None:
        query = query.filter(SourceSystem.tenant_id == tenant_id)
    if system_type is not None:
        query = query.filter(SourceSystem.system_type == system_type)
    if provider is not None:
        query = query.filter(SourceSystem.provider == provider)
    if is_active is not None:
        query = query.filter(SourceSystem.is_active == is_active)
    systems, next_cursor = _paginate_by_id(query, SourceSystem, limit, cursor)
    data = [
        {
            "source_system_id": system.id,
            "tenant_id": system.tenant_id,
            "system_type": system.system_type,
            "provider": system.provider,
            "name": system.name,
            "is_active": system.is_active,
        }
        for system in systems
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


class TenantCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'name': 'Savour Foods', 'is_active': True}}}
    name: str
    is_active: bool = True


@app.post("/api/v1/tenants", tags=["Tenants"])
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)) -> dict:
    tenant = Tenant(
        name=payload.name,
        status="ACTIVE" if payload.is_active else "INACTIVE",
        created_at=_now(),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return {
        "data": {"tenant_id": tenant.id, "name": tenant.name, "is_active": payload.is_active},
        "meta": _meta(),
    }


@app.get("/api/v1/tenants/{tenant_id}", tags=["Tenants"])
def get_tenant(tenant_id: int, db: Session = Depends(get_db)) -> dict:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {
        "data": {
            "tenant_id": tenant.id,
            "name": tenant.name,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tenants", tags=["Tenants"])
def list_tenants(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Tenant)
    if status is not None:
        query = query.filter(Tenant.status == status)
    tenants, next_cursor = _paginate_by_id(query, Tenant, limit, cursor)
    data = [
        {
            "tenant_id": tenant.id,
            "name": tenant.name,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat(),
        }
        for tenant in tenants
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


class LocationCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'name': 'Blue Area', 'timezone': 'Asia/Karachi', 'currency_code': 'PKR', 'is_active': True}}}
    tenant_id: int
    name: str
    timezone: str
    currency_code: str
    is_active: bool = True


@app.post("/api/v1/locations", tags=["Locations"])
def create_location(payload: LocationCreate, db: Session = Depends(get_db)) -> dict:
    location = Location(
        tenant_id=payload.tenant_id,
        name=payload.name,
        timezone=payload.timezone,
        currency_code=payload.currency_code,
        is_active=payload.is_active,
        created_at=_now(),
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return {
        "data": {
            "location_id": location.id,
            "tenant_id": location.tenant_id,
            "name": location.name,
            "timezone": location.timezone,
            "currency_code": location.currency_code,
            "is_active": location.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/locations/{location_id}", tags=["Locations"])
def get_location(location_id: int, db: Session = Depends(get_db)) -> dict:
    location = db.get(Location, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="location not found")
    return {
        "data": {
            "location_id": location.id,
            "tenant_id": location.tenant_id,
            "name": location.name,
            "timezone": location.timezone,
            "currency_code": location.currency_code,
            "is_active": location.is_active,
            "created_at": location.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/locations", tags=["Locations"])
def list_locations(
    tenant_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Location)
    if tenant_id is not None:
        query = query.filter(Location.tenant_id == tenant_id)
    if is_active is not None:
        query = query.filter(Location.is_active == is_active)
    locations, next_cursor = _paginate_by_id(query, Location, limit, cursor)
    data = [
        {
            "location_id": location.id,
            "tenant_id": location.tenant_id,
            "name": location.name,
            "timezone": location.timezone,
            "currency_code": location.currency_code,
            "is_active": location.is_active,
            "created_at": location.created_at.isoformat(),
        }
        for location in locations
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


class LocationHoursInput(BaseModel):
    day_of_week: int
    open_local: str
    close_local: str
    is_closed: bool = False


class LocationHoursUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'business_day_cutover_local': '05:00', 'hours': [{'day_of_week': 0, 'open_local': '10:00', 'close_local': '23:00', 'is_closed': False}, {'day_of_week': 1, 'open_local': '10:00', 'close_local': '23:00', 'is_closed': False}]}}}
    tenant_id: int
    business_day_cutover_local: str
    hours: list[LocationHoursInput]


@app.put("/api/v1/locations/{location_id}/hours", tags=["Location Hours"])
def upsert_location_hours(
    location_id: int, payload: LocationHoursUpsert, db: Session = Depends(get_db)
) -> dict:
    db.query(LocationHours).filter(
        LocationHours.tenant_id == payload.tenant_id,
        LocationHours.location_id == location_id,
    ).delete()
    cutover = _parse_time(payload.business_day_cutover_local)
    rows = []
    for hour in payload.hours:
        row = LocationHours(
            tenant_id=payload.tenant_id,
            location_id=location_id,
            day_of_week=hour.day_of_week,
            open_local=_parse_time(hour.open_local),
            close_local=_parse_time(hour.close_local),
            is_closed=hour.is_closed,
            business_day_cutover_local=cutover,
        )
        rows.append(row)
        db.add(row)
    db.commit()
    return {
        "data": {
            "location_id": location_id,
            "business_day_cutover_local": payload.business_day_cutover_local,
            "hours": [
                {
                    "day_of_week": row.day_of_week,
                    "open_local": row.open_local.strftime("%H:%M"),
                    "close_local": row.close_local.strftime("%H:%M"),
                    "is_closed": row.is_closed,
                }
                for row in rows
            ],
        },
        "meta": _meta(),
    }


class LocationHoursExceptionCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'date_local': '2026-01-20', 'open_local': '12:00', 'close_local': '20:00', 'is_closed': False, 'reason': 'maintenance'}}}
    tenant_id: int
    date_local: date
    open_local: Optional[str] = None
    close_local: Optional[str] = None
    is_closed: bool = False
    reason: Optional[str] = None


@app.post("/api/v1/locations/{location_id}/hours-exceptions", tags=["Location Hours Exceptions"])
def create_hours_exception(
    location_id: int, payload: LocationHoursExceptionCreate, db: Session = Depends(get_db)
) -> dict:
    exception = LocationHoursException(
        tenant_id=payload.tenant_id,
        location_id=location_id,
        date_local=payload.date_local,
        open_local=_parse_time(payload.open_local) if payload.open_local else None,
        close_local=_parse_time(payload.close_local) if payload.close_local else None,
        is_closed=payload.is_closed,
        reason=payload.reason,
    )
    db.add(exception)
    db.commit()
    db.refresh(exception)
    return {
        "data": {
            "exception_id": exception.id,
            "location_id": exception.location_id,
            "date_local": exception.date_local.isoformat(),
            "open_local": exception.open_local.strftime("%H:%M") if exception.open_local else None,
            "close_local": exception.close_local.strftime("%H:%M") if exception.close_local else None,
            "is_closed": exception.is_closed,
            "reason": exception.reason,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/business-days/resolve", tags=["Business Days"])
def resolve_business_day(
    tenant_id: int,
    location_id: int,
    at: datetime,
    db: Session = Depends(get_db),
) -> dict:
    location = db.get(Location, location_id)
    if not location or location.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="location not found")
    tz = ZoneInfo(location.timezone)
    local_at = at.astimezone(tz)
    cutover_time = time(5, 0)
    hours_row = db.query(LocationHours).filter(
        LocationHours.tenant_id == tenant_id,
        LocationHours.location_id == location_id,
    ).first()
    if hours_row:
        cutover_time = hours_row.business_day_cutover_local
    business_date = local_at.date()
    if local_at.time() < cutover_time:
        business_date = business_date - timedelta(days=1)
    starts_at, ends_at = _business_day_bounds(business_date, tz, cutover_time)
    day = db.query(BusinessDay).filter(
        BusinessDay.tenant_id == tenant_id,
        BusinessDay.location_id == location_id,
        BusinessDay.business_date == business_date,
    ).first()
    if not day:
        day = BusinessDay(
            tenant_id=tenant_id,
            location_id=location_id,
            business_date=business_date,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        db.add(day)
        db.commit()
        db.refresh(day)
    return {
        "data": {
            "business_day_id": day.id,
            "tenant_id": tenant_id,
            "location_id": location_id,
            "business_date": business_date.isoformat(),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
        "meta": _meta(),
    }


class BusinessDayEnsure(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'business_date': '2026-01-15'}}}
    tenant_id: int
    location_id: int
    business_date: date


@app.post("/api/v1/business-days:ensure", tags=["Business Days"])
def ensure_business_day(payload: BusinessDayEnsure, db: Session = Depends(get_db)) -> dict:
    location = db.get(Location, payload.location_id)
    if not location or location.tenant_id != payload.tenant_id:
        raise HTTPException(status_code=404, detail="location not found")
    tz = ZoneInfo(location.timezone)
    cutover_time = time(5, 0)
    hours_row = db.query(LocationHours).filter(
        LocationHours.tenant_id == payload.tenant_id,
        LocationHours.location_id == payload.location_id,
    ).first()
    if hours_row:
        cutover_time = hours_row.business_day_cutover_local
    starts_at, ends_at = _business_day_bounds(payload.business_date, tz, cutover_time)
    day = db.query(BusinessDay).filter(
        BusinessDay.tenant_id == payload.tenant_id,
        BusinessDay.location_id == payload.location_id,
        BusinessDay.business_date == payload.business_date,
    ).first()
    if not day:
        day = BusinessDay(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            business_date=payload.business_date,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        db.add(day)
        db.commit()
        db.refresh(day)
    return {
        "data": {
            "business_day_id": day.id,
            "tenant_id": payload.tenant_id,
            "location_id": payload.location_id,
            "business_date": payload.business_date.isoformat(),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
        "meta": _meta(),
    }


class ItemCategoryCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'name': 'Rice', 'parent_category_id': None, 'is_active': True}}}
    tenant_id: int
    name: str
    parent_category_id: Optional[int] = None
    is_active: bool = True


@app.post("/api/v1/item-categories", tags=["Item Categories"])
def create_item_category(payload: ItemCategoryCreate, db: Session = Depends(get_db)) -> dict:
    category = ItemCategory(
        tenant_id=payload.tenant_id,
        name=payload.name,
        parent_category_id=payload.parent_category_id,
        is_active=payload.is_active,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return {
        "data": {
            "item_category_id": category.id,
            "tenant_id": category.tenant_id,
            "name": category.name,
            "parent_category_id": category.parent_category_id,
            "is_active": category.is_active,
        },
        "meta": _meta(),
    }


class ItemCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'item_name': 'Chicken Pulao', 'item_type': 'SELLABLE_MENU_ITEM', 'category_id': 100, 'base_uom': 'portion', 'sellable_flag': True, 'is_active': True}}}
    tenant_id: int
    item_name: str
    item_type: str
    category_id: Optional[int] = None
    base_uom: str
    sellable_flag: bool = False
    is_active: bool = True


@app.post("/api/v1/items", tags=["Items"])
def create_item(payload: ItemCreate, db: Session = Depends(get_db)) -> dict:
    item = Item(
        tenant_id=payload.tenant_id,
        item_name=payload.item_name,
        item_type=payload.item_type,
        category_id=payload.category_id,
        base_uom=payload.base_uom,
        sellable_flag=payload.sellable_flag,
        is_active=payload.is_active,
        created_at=_now(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "data": {
            "item_id": item.id,
            "tenant_id": item.tenant_id,
            "item_name": item.item_name,
            "item_type": item.item_type,
            "category_id": item.category_id,
            "base_uom": item.base_uom,
            "sellable_flag": item.sellable_flag,
            "is_active": item.is_active,
        },
        "meta": _meta(),
    }


class ItemExternalKeyCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'item_id': 2001, 'source_system_id': 1, 'external_item_key': 'SQ-ITEM-8899'}}}
    tenant_id: int
    item_id: int
    source_system_id: int
    external_item_key: str


@app.post("/api/v1/item-external-keys", tags=["Item External Keys"])
def create_item_external_key(payload: ItemExternalKeyCreate, db: Session = Depends(get_db)) -> dict:
    mapping = ItemExternalKey(
        tenant_id=payload.tenant_id,
        item_id=payload.item_id,
        source_system_id=payload.source_system_id,
        external_item_key=payload.external_item_key,
    )
    db.add(mapping)
    db.commit()
    return {
        "data": {
            "item_external_key_id": 0,
            "tenant_id": payload.tenant_id,
            "item_id": payload.item_id,
            "source_system_id": payload.source_system_id,
            "external_item_key": payload.external_item_key,
        },
        "meta": _meta(),
    }


class ChannelMappingCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'provider': 'pos_square', 'source_channel_code': 'DINEIN', 'source_channel_name': 'Dine In', 'normalized': 'DINE_IN'}}}
    tenant_id: int
    provider: Optional[str] = None
    source_channel_code: str
    source_channel_name: Optional[str] = None
    normalized: str


@app.post("/api/v1/channel-mappings", tags=["Channel Mappings"])
def create_channel_mapping(payload: ChannelMappingCreate, db: Session = Depends(get_db)) -> dict:
    mapping = ChannelMapping(
        tenant_id=payload.tenant_id,
        provider=payload.provider,
        source_channel_code=payload.source_channel_code,
        source_channel_name=payload.source_channel_name,
        normalized=payload.normalized,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return {
        "data": {
            "channel_mapping_id": mapping.id,
            "tenant_id": mapping.tenant_id,
            "provider": mapping.provider,
            "source_channel_code": mapping.source_channel_code,
            "source_channel_name": mapping.source_channel_name,
            "normalized": mapping.normalized,
        },
        "meta": _meta(),
    }


class UomConversionCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'scope': 'ITEM', 'item_id': 2001, 'from_uom': 'kg', 'to_uom': 'portion', 'factor': 10.0}}}
    tenant_id: int
    scope: str
    item_id: Optional[int] = None
    from_uom: str
    to_uom: str
    factor: Decimal


@app.post("/api/v1/uom-conversions", tags=["UOM Conversions"])
def create_uom_conversion(payload: UomConversionCreate, db: Session = Depends(get_db)) -> dict:
    conversion = UomConversion(
        tenant_id=payload.tenant_id,
        scope=payload.scope,
        item_id=payload.item_id,
        from_uom=payload.from_uom,
        to_uom=payload.to_uom,
        factor=payload.factor,
    )
    db.add(conversion)
    db.commit()
    db.refresh(conversion)
    return {
        "data": {
            "uom_conversion_id": conversion.id,
            "tenant_id": conversion.tenant_id,
            "scope": conversion.scope,
            "item_id": conversion.item_id,
            "from_uom": conversion.from_uom,
            "to_uom": conversion.to_uom,
            "factor": float(conversion.factor),
        },
        "meta": _meta(),
    }


class ItemCostCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'item_id': 2001, 'currency_code': 'PKR', 'cost_per_base_uom': 85.0, 'effective_from': '2026-01-01', 'effective_to': None, 'source_system_id': 999}}}
    tenant_id: int
    location_id: Optional[int] = None
    item_id: int
    currency_code: str
    cost_per_base_uom: Decimal
    effective_from: date
    effective_to: Optional[date] = None
    source_system_id: Optional[int] = None


@app.post("/api/v1/item-costs", tags=["Item Costs"])
def create_item_cost(payload: ItemCostCreate, db: Session = Depends(get_db)) -> dict:
    cost = ItemCost(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        item_id=payload.item_id,
        currency_code=payload.currency_code,
        cost_per_base_uom=payload.cost_per_base_uom,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        source_system_id=payload.source_system_id,
    )
    db.add(cost)
    db.commit()
    db.refresh(cost)
    return {
        "data": {
            "item_cost_id": cost.id,
            "tenant_id": cost.tenant_id,
            "location_id": cost.location_id,
            "item_id": cost.item_id,
            "currency_code": cost.currency_code,
            "cost_per_base_uom": float(cost.cost_per_base_uom),
            "effective_from": cost.effective_from.isoformat(),
            "effective_to": cost.effective_to.isoformat() if cost.effective_to else None,
            "source_system_id": cost.source_system_id,
        },
        "meta": _meta(),
    }


class TicketLineItemInput(BaseModel):
    source_event_id: str
    external_line_id: Optional[str] = None
    item_id: Optional[int] = None
    external_item_key: Optional[str] = None
    item_name_raw: Optional[str] = None
    qty: Decimal
    uom: Optional[str] = None
    unit_price: Optional[Decimal] = None
    gross_amount: Decimal
    discount_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    net_amount: Decimal


class TicketPaymentInput(BaseModel):
    source_event_id: str
    tender_type: str
    amount: Decimal
    paid_at: datetime
    reference: Optional[str] = None


class TicketDiscountInput(BaseModel):
    source_event_id: str
    reason: Optional[str] = None
    amount: Decimal


class TicketVoidInput(BaseModel):
    source_event_id: str
    reason: Optional[str] = None
    amount: Decimal


class TicketRefundInput(BaseModel):
    source_event_id: str
    reason: Optional[str] = None
    amount: Decimal
    refunded_at: Optional[datetime] = None


class TicketChannelInput(BaseModel):
    provider: Optional[str] = None
    source_channel_code: str
    source_channel_name: Optional[str] = None


class TicketInput(BaseModel):
    source_event_id: str
    external_ticket_id: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    status: str
    covers: Optional[int] = None
    channel: Optional[TicketChannelInput] = None
    gross_amount: Decimal
    discount_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    net_amount: Decimal
    line_items: list[TicketLineItemInput] = Field(default_factory=list)
    payments: list[TicketPaymentInput] = Field(default_factory=list)
    discounts: list[TicketDiscountInput] = Field(default_factory=list)
    voids: list[TicketVoidInput] = Field(default_factory=list)
    refunds: list[TicketRefundInput] = Field(default_factory=list)


class TicketBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 1, 'business_day_id': 900, 'tickets': [{'source_event_id': 'TICKET-10001', 'external_ticket_id': 'SQ-T-10001', 'opened_at': '2026-01-15T12:10:00+05:00', 'closed_at': '2026-01-15T12:40:00+05:00', 'status': 'CLOSED', 'covers': 3, 'channel': {'provider': 'pos_square', 'source_channel_code': 'DINEIN', 'source_channel_name': 'Dine In'}, 'gross_amount': 2500.0, 'discount_amount': 100.0, 'tax_amount': 0.0, 'net_amount': 2400.0, 'line_items': [{'source_event_id': 'TICKET-10001-L1', 'external_line_id': 'SQ-L-1', 'item_id': None, 'external_item_key': 'SQ-ITEM-8899', 'item_name_raw': 'Chicken Pulao', 'qty': 2, 'uom': 'portion', 'unit_price': 1200.0, 'gross_amount': 2400.0, 'discount_amount': 0.0, 'tax_amount': 0.0, 'net_amount': 2400.0}], 'payments': [{'source_event_id': 'TICKET-10001-P1', 'tender_type': 'CARD', 'amount': 2400.0, 'paid_at': '2026-01-15T12:41:00+05:00', 'reference': 'BANKREF-abc'}], 'discounts': [{'source_event_id': 'TICKET-10001-D1', 'reason': 'manager_comp', 'amount': 100.0}], 'voids': [], 'refunds': []}]}}}
    tenant_id: int
    location_id: int
    source_system_id: int
    business_day_id: Optional[int] = None
    tickets: list[TicketInput]


@app.post("/api/v1/ingest/pos/tickets:bulkUpsert", tags=["Ingestion - POS Tickets"])
def bulk_upsert_tickets(payload: TicketBulkUpsert, db: Session = Depends(get_db)) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    results = []
    for ticket_payload in payload.tickets:
        warnings: list[str] = []
        mapping = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == ticket_payload.source_event_id,
            IngestionEventMap.entity_type == "ticket",
        ).first()
        if mapping:
            updated += 1
            results.append(
                {
                    "source_event_id": ticket_payload.source_event_id,
                    "ticket_id": mapping.entity_id,
                    "upsert_status": "UPDATED",
                    "resolved": {},
                    "warnings": warnings,
                }
            )
            continue
        channel_id = None
        if ticket_payload.channel:
            channel = db.query(ChannelMapping).filter(
                ChannelMapping.tenant_id == payload.tenant_id,
                ChannelMapping.provider == ticket_payload.channel.provider,
                ChannelMapping.source_channel_code == ticket_payload.channel.source_channel_code,
            ).first()
            if channel:
                channel_id = channel.id
            else:
                warnings.append("channel_not_mapped")
        ticket = Ticket(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            business_day_id=payload.business_day_id,
            source_system_id=payload.source_system_id,
            external_ticket_id=ticket_payload.external_ticket_id,
            opened_at=ticket_payload.opened_at,
            closed_at=ticket_payload.closed_at,
            status=ticket_payload.status,
            covers=ticket_payload.covers,
            channel_id=channel_id,
            gross_amount=ticket_payload.gross_amount,
            discount_amount=ticket_payload.discount_amount,
            tax_amount=ticket_payload.tax_amount,
            net_amount=ticket_payload.net_amount,
        )
        db.add(ticket)
        db.flush()
        resolved_line_items = []
        for line in ticket_payload.line_items:
            item_id = line.item_id
            mapping_status = None
            if not item_id and line.external_item_key:
                key_map = db.query(ItemExternalKey).filter(
                    ItemExternalKey.tenant_id == payload.tenant_id,
                    ItemExternalKey.source_system_id == payload.source_system_id,
                    ItemExternalKey.external_item_key == line.external_item_key,
                ).first()
                if key_map:
                    item_id = key_map.item_id
                    mapping_status = "MAPPED_BY_EXTERNAL_ITEM_KEY"
                else:
                    mapping_status = "UNMAPPED"
                    warnings.append("item_not_mapped")
            line_item = TicketLineItem(
                tenant_id=payload.tenant_id,
                ticket_id=ticket.id,
                item_id=item_id,
                item_name_raw=line.item_name_raw,
                qty=line.qty,
                uom=line.uom,
                unit_price=line.unit_price,
                gross_line_amount=line.gross_amount,
                discount_line_amount=line.discount_amount,
                tax_line_amount=line.tax_amount,
                net_line_amount=line.net_amount,
                channel_id=channel_id,
            )
            db.add(line_item)
            db.flush()
            db.add(
                IngestionEventMap(
                    tenant_id=payload.tenant_id,
                    source_system_id=payload.source_system_id,
                    source_event_id=line.source_event_id,
                    entity_type="ticket_line_item",
                    entity_id=line_item.id,
                    created_at=_now(),
                )
            )
            resolved_line_items.append(
                {
                    "source_event_id": line.source_event_id,
                    "ticket_line_item_id": line_item.id,
                    "item_id": item_id,
                    "mapping_status": mapping_status,
                }
            )
        for payment in ticket_payload.payments:
            ticket_payment = TicketPayment(
                tenant_id=payload.tenant_id,
                ticket_id=ticket.id,
                paid_at=payment.paid_at,
                tender_type=payment.tender_type,
                amount=payment.amount,
                metadata_json={"reference": payment.reference} if payment.reference else None,
            )
            db.add(ticket_payment)
            db.flush()
            db.add(
                IngestionEventMap(
                    tenant_id=payload.tenant_id,
                    source_system_id=payload.source_system_id,
                    source_event_id=payment.source_event_id,
                    entity_type="ticket_payment",
                    entity_id=ticket_payment.id,
                    created_at=_now(),
                )
            )
        for discount in ticket_payload.discounts:
            ticket_discount = TicketDiscount(
                tenant_id=payload.tenant_id,
                ticket_id=ticket.id,
                amount=discount.amount,
                reason=discount.reason,
            )
            db.add(ticket_discount)
            db.flush()
            db.add(
                IngestionEventMap(
                    tenant_id=payload.tenant_id,
                    source_system_id=payload.source_system_id,
                    source_event_id=discount.source_event_id,
                    entity_type="ticket_discount",
                    entity_id=ticket_discount.id,
                    created_at=_now(),
                )
            )
        for void in ticket_payload.voids:
            ticket_void = TicketVoid(
                tenant_id=payload.tenant_id,
                ticket_id=ticket.id,
                amount=void.amount,
                reason=void.reason,
                voided_at=_now(),
            )
            db.add(ticket_void)
            db.flush()
            db.add(
                IngestionEventMap(
                    tenant_id=payload.tenant_id,
                    source_system_id=payload.source_system_id,
                    source_event_id=void.source_event_id,
                    entity_type="ticket_void",
                    entity_id=ticket_void.id,
                    created_at=_now(),
                )
            )
        for refund in ticket_payload.refunds:
            refunded_at = refund.refunded_at or _now()
            ticket_refund = TicketRefund(
                tenant_id=payload.tenant_id,
                ticket_id=ticket.id,
                amount=refund.amount,
                reason=refund.reason,
                refunded_at=refunded_at,
            )
            db.add(ticket_refund)
            db.flush()
            db.add(
                IngestionEventMap(
                    tenant_id=payload.tenant_id,
                    source_system_id=payload.source_system_id,
                    source_event_id=refund.source_event_id,
                    entity_type="ticket_refund",
                    entity_id=ticket_refund.id,
                    created_at=_now(),
                )
            )
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=ticket_payload.source_event_id,
                entity_type="ticket",
                entity_id=ticket.id,
                created_at=_now(),
            )
        )
        accepted += 1
        results.append(
            {
                "source_event_id": ticket_payload.source_event_id,
                "ticket_id": ticket.id,
                "upsert_status": "UPSERTED",
                "resolved": {
                    "business_day_id": payload.business_day_id,
                    "channel_id": channel_id,
                    "line_items": resolved_line_items,
                },
                "warnings": warnings,
            }
        )
    db.commit()
    return {
        "data": {
            "accepted": accepted,
            "updated": updated,
            "rejected": rejected,
            "results": results,
        },
        "meta": _meta(),
    }


class OpenCloseSignalInput(BaseModel):
    source_event_id: str
    signal_type: str
    occurred_at: datetime
    metadata: Optional[dict] = None


class OpenCloseSignalBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 999, 'business_day_id': 900, 'signals': [{'source_event_id': 'OC-2026-01-15-OPEN', 'signal_type': 'OPEN', 'occurred_at': '2026-01-15T10:05:00+05:00', 'metadata': {'by': 'manager_12'}}]}}}
    tenant_id: int
    location_id: int
    source_system_id: int
    business_day_id: Optional[int] = None
    signals: list[OpenCloseSignalInput]


@app.post("/api/v1/ingest/pos/open-close-signals:bulkUpsert", tags=["Ingestion - Open/Close Signals"])
def bulk_upsert_open_close_signals(
    payload: OpenCloseSignalBulkUpsert, db: Session = Depends(get_db)
) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    for signal in payload.signals:
        existing = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == signal.source_event_id,
            IngestionEventMap.entity_type == "open_close_signal",
        ).first()
        if existing:
            updated += 1
            continue
        row = OpenCloseSignal(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            business_day_id=payload.business_day_id,
            signal_type=signal.signal_type,
            occurred_at=signal.occurred_at,
            source_system_id=payload.source_system_id,
            metadata_json=signal.metadata_json,
        )
        db.add(row)
        db.flush()
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=signal.source_event_id,
                entity_type="open_close_signal",
                entity_id=row.id,
                created_at=_now(),
            )
        )
        accepted += 1
    db.commit()
    return {"data": {"accepted": accepted, "updated": updated, "rejected": rejected}, "meta": _meta()}


class DowntimeEventInput(BaseModel):
    source_event_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class DowntimeBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 1, 'business_day_id': 900, 'events': [{'source_event_id': 'DT-001', 'started_at': '2026-01-15T18:00:00+05:00', 'ended_at': '2026-01-15T18:20:00+05:00', 'reason': 'internet_down', 'metadata': {}}]}}}
    tenant_id: int
    location_id: int
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[DowntimeEventInput]


@app.post("/api/v1/ingest/pos/downtime-events:bulkUpsert", tags=["Ingestion - POS Downtime"])
def bulk_upsert_downtime_events(
    payload: DowntimeBulkUpsert, db: Session = Depends(get_db)
) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    for event in payload.events:
        existing = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == event.source_event_id,
            IngestionEventMap.entity_type == "pos_downtime_event",
        ).first()
        if existing:
            updated += 1
            continue
        row = PosDowntimeEvent(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            started_at=event.started_at,
            ended_at=event.ended_at,
            reason=event.reason,
            source_system_id=payload.source_system_id,
            metadata_json=event.metadata_json,
        )
        db.add(row)
        db.flush()
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=event.source_event_id,
                entity_type="pos_downtime_event",
                entity_id=row.id,
                created_at=_now(),
            )
        )
        accepted += 1
    db.commit()
    return {"data": {"accepted": accepted, "updated": updated, "rejected": rejected}, "meta": _meta()}


class LaborPunchInput(BaseModel):
    source_event_id: str
    employee_id: Optional[int] = None
    employee_external_key: Optional[str] = None
    role: Optional[str] = None
    clock_in: datetime
    clock_out: Optional[datetime] = None
    metadata: Optional[dict] = None


class LaborPunchBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 1, 'business_day_id': 900, 'punches': [{'source_event_id': 'PUNCH-001', 'employee_id': None, 'employee_external_key': 'POS-EMP-12', 'role': 'cashier', 'clock_in': '2026-01-15T09:50:00+05:00', 'clock_out': '2026-01-15T18:00:00+05:00', 'metadata': {'terminal': 'T1'}}]}}}
    tenant_id: int
    location_id: int
    source_system_id: int
    business_day_id: Optional[int] = None
    punches: list[LaborPunchInput]


@app.post("/api/v1/ingest/labor/punches:bulkUpsert", tags=["Ingestion - Labor Punches"])
def bulk_upsert_labor_punches(
    payload: LaborPunchBulkUpsert, db: Session = Depends(get_db)
) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    results = []
    for punch in payload.punches:
        existing = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == punch.source_event_id,
            IngestionEventMap.entity_type == "labor_punch",
        ).first()
        if existing:
            updated += 1
            results.append(
                {
                    "source_event_id": punch.source_event_id,
                    "labor_punch_id": existing.entity_id,
                    "resolved_employee_id": punch.employee_id,
                    "warnings": [],
                }
            )
            continue
        employee_id = punch.employee_id
        if not employee_id and punch.employee_external_key:
            employee = db.query(Employee).filter(
                Employee.tenant_id == payload.tenant_id,
                Employee.location_id == payload.location_id,
                Employee.external_key == punch.employee_external_key,
            ).first()
            if employee:
                employee_id = employee.id
        if not employee_id:
            rejected += 1
            continue
        row = LaborPunch(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            employee_id=employee_id,
            clock_in=punch.clock_in,
            clock_out=punch.clock_out,
            role=punch.role,
            source_system_id=payload.source_system_id,
            metadata_json=punch.metadata_json,
        )
        db.add(row)
        db.flush()
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=punch.source_event_id,
                entity_type="labor_punch",
                entity_id=row.id,
                created_at=_now(),
            )
        )
        accepted += 1
        results.append(
            {
                "source_event_id": punch.source_event_id,
                "labor_punch_id": row.id,
                "resolved_employee_id": employee_id,
                "warnings": [],
            }
        )
    db.commit()
    return {
        "data": {
            "accepted": accepted,
            "updated": updated,
            "rejected": rejected,
            "results": results,
        },
        "meta": _meta(),
    }


class PayoutInput(BaseModel):
    source_event_id: str
    provider: Optional[str] = None
    payout_reference: Optional[str] = None
    status: str
    currency_code: str
    amount: Decimal
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    expected_payout_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    metadata: Optional[dict] = None


class PayoutBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 20, 'payouts': [{'source_event_id': 'FP-PAYOUT-7788', 'provider': 'foodpanda', 'payout_reference': 'bank_stmt_ref_123', 'status': 'PENDING', 'currency_code': 'PKR', 'amount': 12500.0, 'period_start': '2026-01-14T00:00:00+05:00', 'period_end': '2026-01-14T23:59:59+05:00', 'expected_payout_at': '2026-01-17T12:00:00+05:00', 'paid_at': None, 'metadata': {}}]}}}
    tenant_id: int
    location_id: Optional[int] = None
    source_system_id: int
    payouts: list[PayoutInput]


@app.post("/api/v1/ingest/finance/payouts:bulkUpsert", tags=["Ingestion - Payouts"])
def bulk_upsert_payouts(payload: PayoutBulkUpsert, db: Session = Depends(get_db)) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    for payout in payload.payouts:
        existing = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == payout.source_event_id,
            IngestionEventMap.entity_type == "payout",
        ).first()
        if existing:
            updated += 1
            continue
        row = Payout(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            source_system_id=payload.source_system_id,
            provider=payout.provider,
            payout_reference=payout.payout_reference,
            status=payout.status,
            amount=payout.amount,
            currency_code=payout.currency_code,
            period_start=payout.period_start,
            period_end=payout.period_end,
            expected_payout_at=payout.expected_payout_at,
            paid_at=payout.paid_at,
            created_at=_now(),
        )
        db.add(row)
        db.flush()
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=payout.source_event_id,
                entity_type="payout",
                entity_id=row.id,
                created_at=_now(),
            )
        )
        accepted += 1
    db.commit()
    return {"data": {"accepted": accepted, "updated": updated, "rejected": rejected}, "meta": _meta()}


class StockoutInput(BaseModel):
    source_event_id: str
    item_id: Optional[int] = None
    item_name_raw: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class StockoutBulkUpsert(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'source_system_id': 999, 'business_day_id': 900, 'events': [{'source_event_id': '86-CHICKEN-1', 'item_id': 2001, 'item_name_raw': None, 'started_at': '2026-01-15T19:00:00+05:00', 'ended_at': '2026-01-15T20:10:00+05:00', 'reason': 'ran_out', 'metadata': {}}]}}}
    tenant_id: int
    location_id: int
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[StockoutInput]


@app.post("/api/v1/ingest/inventory/stockouts:bulkUpsert", tags=["Ingestion - Stockouts"])
def bulk_upsert_stockouts(payload: StockoutBulkUpsert, db: Session = Depends(get_db)) -> dict:
    accepted = 0
    updated = 0
    rejected = 0
    for event in payload.events:
        existing = db.query(IngestionEventMap).filter(
            IngestionEventMap.tenant_id == payload.tenant_id,
            IngestionEventMap.source_system_id == payload.source_system_id,
            IngestionEventMap.source_event_id == event.source_event_id,
            IngestionEventMap.entity_type == "stockout_event",
        ).first()
        if existing:
            updated += 1
            continue
        row = StockoutEvent(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            item_id=event.item_id,
            item_name_raw=event.item_name_raw,
            started_at=event.started_at,
            ended_at=event.ended_at,
            reason=event.reason,
            source_system_id=payload.source_system_id,
            metadata_json=event.metadata_json,
        )
        db.add(row)
        db.flush()
        db.add(
            IngestionEventMap(
                tenant_id=payload.tenant_id,
                source_system_id=payload.source_system_id,
                source_event_id=event.source_event_id,
                entity_type="stockout_event",
                entity_id=row.id,
                created_at=_now(),
            )
        )
        accepted += 1
    db.commit()
    return {"data": {"accepted": accepted, "updated": updated, "rejected": rejected}, "meta": _meta()}


class EmployeeCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'external_key': 'POS-EMP-12', 'full_name': 'Ali', 'role': 'cashier', 'is_active': True}}}
    tenant_id: int
    location_id: int
    external_key: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: bool = True


@app.post("/api/v1/employees", tags=["Employees"])
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)) -> dict:
    employee = Employee(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        external_key=payload.external_key,
        full_name=payload.full_name,
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return {
        "data": {
            "employee_id": employee.id,
            "tenant_id": employee.tenant_id,
            "location_id": employee.location_id,
            "external_key": employee.external_key,
            "full_name": employee.full_name,
            "role": employee.role,
            "is_active": employee.is_active,
        },
        "meta": _meta(),
    }


class CashCountCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'business_day_id': 900, 'cashier_employee_id': 55, 'expected_cash': 500.0, 'counted_cash': 480.0, 'counted_at': '2026-01-16T01:00:00+05:00', 'metadata': {'notes': 'short by 20'}}}}
    tenant_id: int
    location_id: int
    business_day_id: int
    cashier_employee_id: int
    expected_cash: Decimal
    counted_cash: Decimal
    counted_at: datetime
    metadata: Optional[dict] = None


@app.post("/api/v1/cash-counts", tags=["Cash Counts"])
def create_cash_count(payload: CashCountCreate, db: Session = Depends(get_db)) -> dict:
    variance = payload.counted_cash - payload.expected_cash
    cash_count = CashCount(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        business_day_id=payload.business_day_id,
        cashier_employee_id=payload.cashier_employee_id,
        expected_cash=payload.expected_cash,
        counted_cash=payload.counted_cash,
        variance=variance,
        counted_at=payload.counted_at,
        metadata_json=payload.metadata_json,
    )
    db.add(cash_count)
    db.commit()
    db.refresh(cash_count)
    return {
        "data": {
            "cash_count_id": cash_count.id,
            "tenant_id": cash_count.tenant_id,
            "location_id": cash_count.location_id,
            "business_day_id": cash_count.business_day_id,
            "cashier_employee_id": cash_count.cashier_employee_id,
            "expected_cash": float(cash_count.expected_cash or 0),
            "counted_cash": float(cash_count.counted_cash or 0),
            "variance": float(cash_count.variance or 0),
            "counted_at": cash_count.counted_at.isoformat(),
            "metadata": cash_count.metadata_json,
        },
        "meta": _meta(),
    }


class AssetCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'asset_name': 'Rice Cooker #1', 'asset_type': 'kitchen', 'serial_number': 'RC-001', 'is_critical': True, 'is_active': True}}}
    tenant_id: int
    location_id: int
    asset_name: str
    asset_type: str
    serial_number: Optional[str] = None
    is_critical: bool = False
    is_active: bool = True


@app.post("/api/v1/assets", tags=["Assets"])
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)) -> dict:
    location = db.get(Location, payload.location_id)
    if not location or location.tenant_id != payload.tenant_id:
        raise HTTPException(status_code=400, detail="invalid tenant_id or location_id")
    asset = Asset(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        asset_name=payload.asset_name,
        asset_type=payload.asset_type,
        serial_number=payload.serial_number,
        is_critical=payload.is_critical,
        is_active=payload.is_active,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {
        "data": {
            "asset_id": asset.id,
            "tenant_id": asset.tenant_id,
            "location_id": asset.location_id,
            "asset_name": asset.asset_name,
            "asset_type": asset.asset_type,
            "serial_number": asset.serial_number,
            "is_critical": asset.is_critical,
            "is_active": asset.is_active,
        },
        "meta": _meta(),
    }


class IncidentCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'asset_id': 910, 'incident_type': 'equipment_failure', 'status': 'OPEN', 'opened_at': '2026-01-15T17:00:00+05:00', 'title': 'Cooker not heating', 'description': 'Cooker not heating properly', 'source_system_id': 999, 'metadata': {'reported_by': 'manager_12'}}}}
    tenant_id: int
    location_id: int
    asset_id: Optional[int] = None
    incident_type: str
    status: str
    opened_at: datetime
    title: Optional[str] = None
    description: Optional[str] = None
    source_system_id: Optional[int] = None
    metadata: Optional[dict] = None


@app.post("/api/v1/incidents", tags=["Incidents"])
def create_incident(payload: IncidentCreate, db: Session = Depends(get_db)) -> dict:
    incident = Incident(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        asset_id=payload.asset_id,
        incident_type=payload.incident_type,
        status=payload.status,
        opened_at=payload.opened_at,
        title=payload.title,
        description=payload.description,
        source_system_id=payload.source_system_id,
        metadata_json=payload.metadata_json,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return {
        "data": {
            "incident_id": incident.id,
            "tenant_id": incident.tenant_id,
            "location_id": incident.location_id,
            "asset_id": incident.asset_id,
            "incident_type": incident.incident_type,
            "status": incident.status,
            "opened_at": incident.opened_at.isoformat(),
            "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
        },
        "meta": _meta(),
    }


class WorkOrderCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'incident_id': 1200, 'asset_id': 910, 'status': 'OPEN', 'due_at': '2026-01-16T12:00:00+05:00', 'description': 'Call technician', 'metadata': {'priority': 'high'}}}}
    tenant_id: int
    location_id: int
    incident_id: Optional[int] = None
    asset_id: Optional[int] = None
    status: str
    due_at: Optional[datetime] = None
    description: Optional[str] = None
    metadata: Optional[dict] = None


@app.post("/api/v1/work-orders", tags=["Work Orders"])
def create_work_order(payload: WorkOrderCreate, db: Session = Depends(get_db)) -> dict:
    work_order = WorkOrder(
        tenant_id=payload.tenant_id,
        location_id=payload.location_id,
        incident_id=payload.incident_id,
        asset_id=payload.asset_id,
        status=payload.status,
        created_at=_now(),
        due_at=payload.due_at,
        description=payload.description,
        metadata_json=payload.metadata_json,
    )
    db.add(work_order)
    db.commit()
    db.refresh(work_order)
    return {
        "data": {
            "work_order_id": work_order.id,
            "status": work_order.status,
            "due_at": work_order.due_at.isoformat() if work_order.due_at else None,
            "completed_at": work_order.completed_at.isoformat() if work_order.completed_at else None,
        },
        "meta": _meta(),
    }


class PolicyCreate(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'policy_version': '2026-01-01.v1', 'effective_from': '2026-01-01', 'config': {'truth_labels': {'payout_lag_hours': 48}, 'thresholds': {'discount_rate_warn': 0.05}}}}}
    tenant_id: int
    policy_version: str
    effective_from: date
    config: dict


@app.post("/api/v1/policies", tags=["Policies"])
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)) -> dict:
    policy = Policy(
        tenant_id=payload.tenant_id,
        policy_version=payload.policy_version,
        effective_from=payload.effective_from,
        config=payload.config,
        created_at=_now(),
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return {
        "data": {
            "policy_id": policy.id,
            "tenant_id": policy.tenant_id,
            "policy_version": policy.policy_version,
            "effective_from": policy.effective_from.isoformat(),
            "created_at": policy.created_at.isoformat(),
        },
        "meta": _meta(),
    }


class SnapshotBuild(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'location_id': 10, 'business_day_id': 900, 'force_rebuild': False}}}
    tenant_id: int
    location_id: int
    business_day_id: int
    force_rebuild: bool = False


@app.post("/api/v1/snapshots:build", tags=["Snapshots"])
def build_snapshot(payload: SnapshotBuild, db: Session = Depends(get_db)) -> dict:
    existing = None
    if not payload.force_rebuild:
        existing = db.query(Snapshot).filter(
            Snapshot.tenant_id == payload.tenant_id,
            Snapshot.location_id == payload.location_id,
            Snapshot.business_day_id == payload.business_day_id,
        ).first()
    if existing:
        snapshot = existing
    else:
        snapshot = Snapshot(
            tenant_id=payload.tenant_id,
            location_id=payload.location_id,
            business_day_id=payload.business_day_id,
            summary_hash=f"sha256:{uuid4().hex}",
            day_state_object={},
            snapshot_metadata={
                "numbers_status": "PRELIM",
                "feed_completeness_flags": {
                    "pos": "OK",
                    "payments": "OK",
                    "labor": "MISSING",
                    "payouts": "PARTIAL",
                    "inventory": "OK",
                },
            },
            created_at=_now(),
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
    return {
        "data": {
            "snapshot_id": snapshot.id,
            "tenant_id": snapshot.tenant_id,
            "location_id": snapshot.location_id,
            "business_day_id": snapshot.business_day_id,
            "numbers_status": (snapshot.snapshot_metadata or {}).get("numbers_status"),
            "completeness": (snapshot.snapshot_metadata or {}).get("feed_completeness_flags"),
            "summary_hash": snapshot.summary_hash,
            "created_at": snapshot.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/snapshots/{snapshot_id}", tags=["Snapshots"])
def get_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> dict:
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {
        "data": {
            "snapshot_id": snapshot.id,
            "tenant_id": snapshot.tenant_id,
            "location_id": snapshot.location_id,
            "business_day_id": snapshot.business_day_id,
            "numbers_status": (snapshot.snapshot_metadata or {}).get("numbers_status"),
            "feed_completeness_flags": (snapshot.snapshot_metadata or {}).get(
                "feed_completeness_flags"
            ),
            "snapshot_metadata": snapshot.snapshot_metadata or {},
            "day_state_object": snapshot.day_state_object or {
                "sales": {},
                "labor": {},
                "payouts": {},
                "flags": [],
            },
            "summary_hash": snapshot.summary_hash,
            "created_at": snapshot.created_at.isoformat(),
        },
        "meta": _meta(),
    }


class ReportRunBuild(BaseModel):
    model_config = {"json_schema_extra": {"example": {'tenant_id': 1, 'snapshot_id': 88001, 'policy_version': '2026-01-01.v1', 'report_version': 'v2'}}}
    tenant_id: int
    snapshot_id: int
    policy_version: str
    report_version: str


@app.post("/api/v1/report-runs:build", tags=["Report Runs"])
def build_report_run(payload: ReportRunBuild, db: Session = Depends(get_db)) -> dict:
    snapshot = db.get(Snapshot, payload.snapshot_id)
    if not snapshot or snapshot.tenant_id != payload.tenant_id:
        raise HTTPException(status_code=404, detail="snapshot not found")
    completeness = (snapshot.snapshot_metadata or {}).get("feed_completeness_flags")
    report_run = ReportRun(
        tenant_id=payload.tenant_id,
        location_id=snapshot.location_id,
        business_day_id=snapshot.business_day_id,
        snapshot_id=payload.snapshot_id,
        report_version=payload.report_version,
        generated_at=_now(),
        numbers_status="PRELIM",
        feed_completeness_flags=completeness,
        truth_labels={},
        kpis={"net_sales": 2400.0, "discount_rate": 0.04},
        alerts=[],
        ops_issues=[],
        recommendations=[],
        metadata_json={
            "policy_version": payload.policy_version,
            "render_manifest": {"template": "daily_report_v2", "channels": ["whatsapp"]},
        },
    )
    db.add(report_run)
    db.commit()
    db.refresh(report_run)
    return {
        "data": {
            "report_run_id": report_run.id,
            "tenant_id": report_run.tenant_id,
            "snapshot_id": report_run.snapshot_id,
            "policy_version": (report_run.metadata_json or {}).get("policy_version"),
            "report_version": report_run.report_version,
            "status": "COMPLETED",
            "outputs": {
                "truth_labels": report_run.truth_labels or {},
                "kpis": report_run.kpis or {},
                "alerts": report_run.alerts or [],
                "ops_issues": report_run.ops_issues or [],
                "recommendations": report_run.recommendations or [],
                "render_manifest": (report_run.metadata_json or {}).get(
                    "render_manifest", {}
                ),
            },
            "created_at": report_run.generated_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/locations/{location_id}/hours", tags=["Location Hours"])
def get_location_hours(
    location_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.query(LocationHours).filter(
        LocationHours.tenant_id == tenant_id,
        LocationHours.location_id == location_id,
    ).order_by(LocationHours.id).all()
    return {
        "data": {
            "tenant_id": tenant_id,
            "location_id": location_id,
            "hours": [
                {
                    "location_hours_id": row.id,
                    "day_of_week": row.day_of_week,
                    "open_local": row.open_local.strftime("%H:%M:%S"),
                    "close_local": row.close_local.strftime("%H:%M:%S"),
                    "business_day_cutover_local": row.business_day_cutover_local.strftime(
                        "%H:%M:%S"
                    ),
                    "is_closed": row.is_closed,
                }
                for row in rows
            ],
        },
        "meta": _meta(),
    }


@app.get("/api/v1/location-hours", tags=["Location Hours"])
def list_location_hours(
    tenant_id: int = Query(...),
    location_id: Optional[int] = Query(default=None),
    day_of_week: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(LocationHours).filter(LocationHours.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(LocationHours.location_id == location_id)
    if day_of_week is not None:
        query = query.filter(LocationHours.day_of_week == day_of_week)
    rows, next_cursor = _paginate_by_id(query, LocationHours, limit, cursor)
    data = [
        {
            "location_hours_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "day_of_week": row.day_of_week,
            "open_local": row.open_local.strftime("%H:%M:%S"),
            "close_local": row.close_local.strftime("%H:%M:%S"),
            "business_day_cutover_local": row.business_day_cutover_local.strftime(
                "%H:%M:%S"
            ),
            "is_closed": row.is_closed,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/locations/{location_id}/hours-exceptions", tags=["Location Hours Exceptions"])
def list_location_hours_exceptions(
    location_id: int,
    tenant_id: int = Query(...),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(LocationHoursException).filter(
        LocationHoursException.tenant_id == tenant_id,
        LocationHoursException.location_id == location_id,
    )
    if from_date is not None:
        query = query.filter(LocationHoursException.date_local >= from_date)
    if to_date is not None:
        query = query.filter(LocationHoursException.date_local <= to_date)
    rows, next_cursor = _paginate_by_id(query, LocationHoursException, limit, cursor)
    data = [
        {
            "exception_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "date_local": row.date_local.isoformat(),
            "open_local": row.open_local.strftime("%H:%M:%S") if row.open_local else None,
            "close_local": row.close_local.strftime("%H:%M:%S") if row.close_local else None,
            "is_closed": row.is_closed,
            "reason": row.reason,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/location-hours-exceptions/{exception_id}", tags=["Location Hours Exceptions"])
def get_location_hours_exception(exception_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(LocationHoursException, exception_id)
    if not row:
        raise HTTPException(status_code=404, detail="exception not found")
    return {
        "data": {
            "exception_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "date_local": row.date_local.isoformat(),
            "open_local": row.open_local.strftime("%H:%M:%S") if row.open_local else None,
            "close_local": row.close_local.strftime("%H:%M:%S") if row.close_local else None,
            "is_closed": row.is_closed,
            "reason": row.reason,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/business-days/{business_day_id}", tags=["Business Days"])
def get_business_day(business_day_id: int, db: Session = Depends(get_db)) -> dict:
    day = db.get(BusinessDay, business_day_id)
    if not day:
        raise HTTPException(status_code=404, detail="business day not found")
    return {
        "data": {
            "business_day_id": day.id,
            "tenant_id": day.tenant_id,
            "location_id": day.location_id,
            "business_date": day.business_date.isoformat(),
            "starts_at": day.starts_at.isoformat(),
            "ends_at": day.ends_at.isoformat(),
            "planned_open_at": day.planned_open_at.isoformat() if day.planned_open_at else None,
            "planned_close_at": day.planned_close_at.isoformat() if day.planned_close_at else None,
            "actual_open_at": day.actual_open_at.isoformat() if day.actual_open_at else None,
            "actual_close_at": day.actual_close_at.isoformat() if day.actual_close_at else None,
            "late_open": day.late_open,
            "early_close": day.early_close,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/business-days", tags=["Business Days"])
def list_business_days(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(BusinessDay)
    if tenant_id is not None:
        query = query.filter(BusinessDay.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(BusinessDay.location_id == location_id)
    if from_date is not None:
        query = query.filter(BusinessDay.business_date >= from_date)
    if to_date is not None:
        query = query.filter(BusinessDay.business_date <= to_date)
    rows, next_cursor = _paginate_by_id(query, BusinessDay, limit, cursor)
    data = [
        {
            "business_day_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "business_date": row.business_date.isoformat(),
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
            "planned_open_at": row.planned_open_at.isoformat() if row.planned_open_at else None,
            "planned_close_at": row.planned_close_at.isoformat() if row.planned_close_at else None,
            "actual_open_at": row.actual_open_at.isoformat() if row.actual_open_at else None,
            "actual_close_at": row.actual_close_at.isoformat() if row.actual_close_at else None,
            "late_open": row.late_open,
            "early_close": row.early_close,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/item-categories/{item_category_id}", tags=["Item Categories"])
def get_item_category(item_category_id: int, db: Session = Depends(get_db)) -> dict:
    category = db.get(ItemCategory, item_category_id)
    if not category:
        raise HTTPException(status_code=404, detail="item category not found")
    return {
        "data": {
            "item_category_id": category.id,
            "tenant_id": category.tenant_id,
            "name": category.name,
            "parent_category_id": category.parent_category_id,
            "is_active": category.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/item-categories", tags=["Item Categories"])
def list_item_categories(
    tenant_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    parent_category_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ItemCategory)
    if tenant_id is not None:
        query = query.filter(ItemCategory.tenant_id == tenant_id)
    if is_active is not None:
        query = query.filter(ItemCategory.is_active == is_active)
    if parent_category_id is not None:
        query = query.filter(ItemCategory.parent_category_id == parent_category_id)
    rows, next_cursor = _paginate_by_id(query, ItemCategory, limit, cursor)
    data = [
        {
            "item_category_id": row.id,
            "tenant_id": row.tenant_id,
            "name": row.name,
            "parent_category_id": row.parent_category_id,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/items/{item_id}", tags=["Items"])
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return {
        "data": {
            "item_id": item.id,
            "tenant_id": item.tenant_id,
            "item_name": item.item_name,
            "item_type": item.item_type,
            "category_id": item.category_id,
            "is_active": item.is_active,
            "base_uom": item.base_uom,
            "sellable_flag": item.sellable_flag,
            "recipe_id": item.recipe_id,
            "bom_id": item.bom_id,
            "created_at": item.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/items", tags=["Items"])
def list_items(
    tenant_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    category_id: Optional[int] = Query(default=None),
    item_type: Optional[str] = Query(default=None),
    sellable_flag: Optional[bool] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Item)
    if tenant_id is not None:
        query = query.filter(Item.tenant_id == tenant_id)
    if is_active is not None:
        query = query.filter(Item.is_active == is_active)
    if category_id is not None:
        query = query.filter(Item.category_id == category_id)
    if item_type is not None:
        query = query.filter(Item.item_type == item_type)
    if sellable_flag is not None:
        query = query.filter(Item.sellable_flag == sellable_flag)
    if q is not None:
        query = query.filter(Item.item_name.ilike(f"%{q}%"))
    rows, next_cursor = _paginate_by_id(query, Item, limit, cursor)
    data = [
        {
            "item_id": row.id,
            "tenant_id": row.tenant_id,
            "item_name": row.item_name,
            "item_type": row.item_type,
            "category_id": row.category_id,
            "is_active": row.is_active,
            "base_uom": row.base_uom,
            "sellable_flag": row.sellable_flag,
            "recipe_id": row.recipe_id,
            "bom_id": row.bom_id,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/item-external-keys", tags=["Item External Keys"])
def list_item_external_keys(
    tenant_id: int = Query(...),
    source_system_id: Optional[int] = Query(default=None),
    item_id: Optional[int] = Query(default=None),
    external_item_key: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ItemExternalKey).filter(ItemExternalKey.tenant_id == tenant_id)
    if source_system_id is not None:
        query = query.filter(ItemExternalKey.source_system_id == source_system_id)
    if item_id is not None:
        query = query.filter(ItemExternalKey.item_id == item_id)
    if external_item_key is not None:
        query = query.filter(ItemExternalKey.external_item_key == external_item_key)
    rows, next_cursor = _paginate_by_offset(query, limit, cursor)
    data = [
        {
            "tenant_id": row.tenant_id,
            "item_id": row.item_id,
            "source_system_id": row.source_system_id,
            "external_item_key": row.external_item_key,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/items/{item_id}/external-keys", tags=["Item External Keys"])
def list_item_external_keys_for_item(
    item_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ItemExternalKey).filter(
        ItemExternalKey.tenant_id == tenant_id,
        ItemExternalKey.item_id == item_id,
    )
    rows, next_cursor = _paginate_by_offset(query, limit, cursor)
    data = [
        {
            "tenant_id": row.tenant_id,
            "item_id": row.item_id,
            "source_system_id": row.source_system_id,
            "external_item_key": row.external_item_key,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/external-id-map", tags=["External ID Map"])
def list_external_id_map(
    tenant_id: int = Query(...),
    source_system_id: Optional[int] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    external_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ExternalIdMap).filter(ExternalIdMap.tenant_id == tenant_id)
    if source_system_id is not None:
        query = query.filter(ExternalIdMap.source_system_id == source_system_id)
    if entity_type is not None:
        query = query.filter(ExternalIdMap.entity_type == entity_type)
    if external_id is not None:
        query = query.filter(ExternalIdMap.external_id == external_id)
    rows, next_cursor = _paginate_by_offset(query, limit, cursor)
    data = [
        {
            "tenant_id": row.tenant_id,
            "source_system_id": row.source_system_id,
            "entity_type": row.entity_type,
            "external_id": row.external_id,
            "internal_id": row.internal_id,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/channel-mappings/{channel_mapping_id}", tags=["Channel Mappings"])
def get_channel_mapping(channel_mapping_id: int, db: Session = Depends(get_db)) -> dict:
    mapping = db.get(ChannelMapping, channel_mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="channel mapping not found")
    return {
        "data": {
            "channel_mapping_id": mapping.id,
            "tenant_id": mapping.tenant_id,
            "provider": mapping.provider,
            "source_channel_code": mapping.source_channel_code,
            "source_channel_name": mapping.source_channel_name,
            "normalized": mapping.normalized,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/channel-mappings", tags=["Channel Mappings"])
def list_channel_mappings(
    tenant_id: Optional[int] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    normalized: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ChannelMapping)
    if tenant_id is not None:
        query = query.filter(ChannelMapping.tenant_id == tenant_id)
    if provider is not None:
        query = query.filter(ChannelMapping.provider == provider)
    if normalized is not None:
        query = query.filter(ChannelMapping.normalized == normalized)
    rows, next_cursor = _paginate_by_id(query, ChannelMapping, limit, cursor)
    data = [
        {
            "channel_mapping_id": row.id,
            "tenant_id": row.tenant_id,
            "provider": row.provider,
            "source_channel_code": row.source_channel_code,
            "source_channel_name": row.source_channel_name,
            "normalized": row.normalized,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/uom-conversions/{uom_conversion_id}", tags=["UOM Conversions"])
def get_uom_conversion(uom_conversion_id: int, db: Session = Depends(get_db)) -> dict:
    conversion = db.get(UomConversion, uom_conversion_id)
    if not conversion:
        raise HTTPException(status_code=404, detail="uom conversion not found")
    return {
        "data": {
            "uom_conversion_id": conversion.id,
            "tenant_id": conversion.tenant_id,
            "scope": conversion.scope,
            "item_id": conversion.item_id,
            "from_uom": conversion.from_uom,
            "to_uom": conversion.to_uom,
            "factor": float(conversion.factor),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/uom-conversions", tags=["UOM Conversions"])
def list_uom_conversions(
    tenant_id: Optional[int] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    item_id: Optional[int] = Query(default=None),
    from_uom: Optional[str] = Query(default=None),
    to_uom: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(UomConversion)
    if tenant_id is not None:
        query = query.filter(UomConversion.tenant_id == tenant_id)
    if scope is not None:
        query = query.filter(UomConversion.scope == scope)
    if item_id is not None:
        query = query.filter(UomConversion.item_id == item_id)
    if from_uom is not None:
        query = query.filter(UomConversion.from_uom == from_uom)
    if to_uom is not None:
        query = query.filter(UomConversion.to_uom == to_uom)
    rows, next_cursor = _paginate_by_id(query, UomConversion, limit, cursor)
    data = [
        {
            "uom_conversion_id": row.id,
            "tenant_id": row.tenant_id,
            "scope": row.scope,
            "item_id": row.item_id,
            "from_uom": row.from_uom,
            "to_uom": row.to_uom,
            "factor": float(row.factor),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/item-costs/{item_cost_id}", tags=["Item Costs"])
def get_item_cost(item_cost_id: int, db: Session = Depends(get_db)) -> dict:
    cost = db.get(ItemCost, item_cost_id)
    if not cost:
        raise HTTPException(status_code=404, detail="item cost not found")
    return {
        "data": {
            "item_cost_id": cost.id,
            "tenant_id": cost.tenant_id,
            "item_id": cost.item_id,
            "location_id": cost.location_id,
            "cost_per_base_uom": float(cost.cost_per_base_uom),
            "currency_code": cost.currency_code,
            "effective_from": cost.effective_from.isoformat(),
            "effective_to": cost.effective_to.isoformat() if cost.effective_to else None,
            "source_system_id": cost.source_system_id,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/item-costs", tags=["Item Costs"])
def list_item_costs(
    tenant_id: Optional[int] = Query(default=None),
    item_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    active_at: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ItemCost)
    if tenant_id is not None:
        query = query.filter(ItemCost.tenant_id == tenant_id)
    if item_id is not None:
        query = query.filter(ItemCost.item_id == item_id)
    if location_id is not None:
        query = query.filter(ItemCost.location_id == location_id)
    if active_at is not None:
        query = query.filter(ItemCost.effective_from <= active_at).filter(
            (ItemCost.effective_to.is_(None)) | (ItemCost.effective_to >= active_at)
        )
    rows, next_cursor = _paginate_by_id(query, ItemCost, limit, cursor)
    data = [
        {
            "item_cost_id": row.id,
            "tenant_id": row.tenant_id,
            "item_id": row.item_id,
            "location_id": row.location_id,
            "cost_per_base_uom": float(row.cost_per_base_uom),
            "currency_code": row.currency_code,
            "effective_from": row.effective_from.isoformat(),
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "source_system_id": row.source_system_id,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/recipes/{recipe_id}", tags=["Recipes"])
def get_recipe(recipe_id: int, db: Session = Depends(get_db)) -> dict:
    recipe = db.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="recipe not found")
    return {
        "data": {
            "recipe_id": recipe.id,
            "tenant_id": recipe.tenant_id,
            "output_item_id": recipe.output_item_id,
            "output_qty": float(recipe.output_qty),
            "output_uom": recipe.output_uom,
            "is_active": recipe.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/recipes", tags=["Recipes"])
def list_recipes(
    tenant_id: Optional[int] = Query(default=None),
    output_item_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Recipe)
    if tenant_id is not None:
        query = query.filter(Recipe.tenant_id == tenant_id)
    if output_item_id is not None:
        query = query.filter(Recipe.output_item_id == output_item_id)
    if is_active is not None:
        query = query.filter(Recipe.is_active == is_active)
    rows, next_cursor = _paginate_by_id(query, Recipe, limit, cursor)
    data = [
        {
            "recipe_id": row.id,
            "tenant_id": row.tenant_id,
            "output_item_id": row.output_item_id,
            "output_qty": float(row.output_qty),
            "output_uom": row.output_uom,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/recipe-components/{recipe_component_id}", tags=["Recipe Components"])
def get_recipe_component(recipe_component_id: int, db: Session = Depends(get_db)) -> dict:
    component = db.get(RecipeComponent, recipe_component_id)
    if not component:
        raise HTTPException(status_code=404, detail="recipe component not found")
    return {
        "data": {
            "recipe_component_id": component.id,
            "tenant_id": component.tenant_id,
            "recipe_id": component.recipe_id,
            "component_item_id": component.component_item_id,
            "qty": float(component.qty),
            "uom": component.uom,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/recipes/{recipe_id}/components", tags=["Recipe Components"])
def list_recipe_components_for_recipe(
    recipe_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(RecipeComponent).filter(
        RecipeComponent.tenant_id == tenant_id,
        RecipeComponent.recipe_id == recipe_id,
    )
    rows, next_cursor = _paginate_by_id(query, RecipeComponent, limit, cursor)
    data = [
        {
            "recipe_component_id": row.id,
            "tenant_id": row.tenant_id,
            "recipe_id": row.recipe_id,
            "component_item_id": row.component_item_id,
            "qty": float(row.qty),
            "uom": row.uom,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/recipe-components", tags=["Recipe Components"])
def list_recipe_components(
    tenant_id: Optional[int] = Query(default=None),
    recipe_id: Optional[int] = Query(default=None),
    component_item_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(RecipeComponent)
    if tenant_id is not None:
        query = query.filter(RecipeComponent.tenant_id == tenant_id)
    if recipe_id is not None:
        query = query.filter(RecipeComponent.recipe_id == recipe_id)
    if component_item_id is not None:
        query = query.filter(RecipeComponent.component_item_id == component_item_id)
    rows, next_cursor = _paginate_by_id(query, RecipeComponent, limit, cursor)
    data = [
        {
            "recipe_component_id": row.id,
            "tenant_id": row.tenant_id,
            "recipe_id": row.recipe_id,
            "component_item_id": row.component_item_id,
            "qty": float(row.qty),
            "uom": row.uom,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/employees/{employee_id}", tags=["Employees"])
def get_employee(employee_id: int, db: Session = Depends(get_db)) -> dict:
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="employee not found")
    return {
        "data": {
            "employee_id": employee.id,
            "tenant_id": employee.tenant_id,
            "location_id": employee.location_id,
            "external_key": employee.external_key,
            "full_name": employee.full_name,
            "role": employee.role,
            "is_active": employee.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/employees", tags=["Employees"])
def list_employees(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    role: Optional[str] = Query(default=None),
    external_key: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Employee)
    if tenant_id is not None:
        query = query.filter(Employee.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Employee.location_id == location_id)
    if is_active is not None:
        query = query.filter(Employee.is_active == is_active)
    if role is not None:
        query = query.filter(Employee.role == role)
    if external_key is not None:
        query = query.filter(Employee.external_key == external_key)
    rows, next_cursor = _paginate_by_id(query, Employee, limit, cursor)
    data = [
        {
            "employee_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "external_key": row.external_key,
            "full_name": row.full_name,
            "role": row.role,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/labor-punches/{labor_punch_id}", tags=["Labor Punches"])
def get_labor_punch(labor_punch_id: int, db: Session = Depends(get_db)) -> dict:
    punch = db.get(LaborPunch, labor_punch_id)
    if not punch:
        raise HTTPException(status_code=404, detail="labor punch not found")
    return {
        "data": {
            "labor_punch_id": punch.id,
            "tenant_id": punch.tenant_id,
            "location_id": punch.location_id,
            "employee_id": punch.employee_id,
            "clock_in": punch.clock_in.isoformat(),
            "clock_out": punch.clock_out.isoformat() if punch.clock_out else None,
            "role": punch.role,
            "source_system_id": punch.source_system_id,
            "metadata": punch.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/labor-punches", tags=["Labor Punches"])
def list_labor_punches(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    employee_id: Optional[int] = Query(default=None),
    from_ts: Optional[datetime] = Query(default=None, alias="from"),
    to_ts: Optional[datetime] = Query(default=None, alias="to"),
    source_system_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(LaborPunch)
    if tenant_id is not None:
        query = query.filter(LaborPunch.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(LaborPunch.location_id == location_id)
    if employee_id is not None:
        query = query.filter(LaborPunch.employee_id == employee_id)
    if from_ts is not None:
        query = query.filter(LaborPunch.clock_in >= from_ts)
    if to_ts is not None:
        query = query.filter(LaborPunch.clock_in <= to_ts)
    if source_system_id is not None:
        query = query.filter(LaborPunch.source_system_id == source_system_id)
    rows, next_cursor = _paginate_by_id(query, LaborPunch, limit, cursor)
    data = [
        {
            "labor_punch_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "employee_id": row.employee_id,
            "clock_in": row.clock_in.isoformat(),
            "clock_out": row.clock_out.isoformat() if row.clock_out else None,
            "role": row.role,
            "source_system_id": row.source_system_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/labor-cost-rates/{labor_cost_rate_id}", tags=["Labor Cost Rates"])
def get_labor_cost_rate(labor_cost_rate_id: int, db: Session = Depends(get_db)) -> dict:
    rate = db.get(LaborCostRate, labor_cost_rate_id)
    if not rate:
        raise HTTPException(status_code=404, detail="labor cost rate not found")
    return {
        "data": {
            "labor_cost_rate_id": rate.id,
            "tenant_id": rate.tenant_id,
            "employee_id": rate.employee_id,
            "role": rate.role,
            "hourly_rate": float(rate.hourly_rate),
            "currency_code": rate.currency_code,
            "effective_from": rate.effective_from.isoformat(),
            "effective_to": rate.effective_to.isoformat() if rate.effective_to else None,
            "payroll_code": rate.payroll_code,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/labor-cost-rates", tags=["Labor Cost Rates"])
def list_labor_cost_rates(
    tenant_id: Optional[int] = Query(default=None),
    employee_id: Optional[int] = Query(default=None),
    role: Optional[str] = Query(default=None),
    active_at: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(LaborCostRate)
    if tenant_id is not None:
        query = query.filter(LaborCostRate.tenant_id == tenant_id)
    if employee_id is not None:
        query = query.filter(LaborCostRate.employee_id == employee_id)
    if role is not None:
        query = query.filter(LaborCostRate.role == role)
    if active_at is not None:
        query = query.filter(LaborCostRate.effective_from <= active_at).filter(
            (LaborCostRate.effective_to.is_(None)) | (LaborCostRate.effective_to >= active_at)
        )
    rows, next_cursor = _paginate_by_id(query, LaborCostRate, limit, cursor)
    data = [
        {
            "labor_cost_rate_id": row.id,
            "tenant_id": row.tenant_id,
            "employee_id": row.employee_id,
            "role": row.role,
            "hourly_rate": float(row.hourly_rate),
            "currency_code": row.currency_code,
            "effective_from": row.effective_from.isoformat(),
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "payroll_code": row.payroll_code,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/pos-terminals/{terminal_id}", tags=["POS Terminals"])
def get_pos_terminal(terminal_id: int, db: Session = Depends(get_db)) -> dict:
    terminal = db.get(PosTerminal, terminal_id)
    if not terminal:
        raise HTTPException(status_code=404, detail="pos terminal not found")
    return {
        "data": {
            "terminal_id": terminal.id,
            "tenant_id": terminal.tenant_id,
            "location_id": terminal.location_id,
            "terminal_code": terminal.terminal_code,
            "is_active": terminal.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/pos-terminals", tags=["POS Terminals"])
def list_pos_terminals(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(PosTerminal)
    if tenant_id is not None:
        query = query.filter(PosTerminal.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(PosTerminal.location_id == location_id)
    if is_active is not None:
        query = query.filter(PosTerminal.is_active == is_active)
    rows, next_cursor = _paginate_by_id(query, PosTerminal, limit, cursor)
    data = [
        {
            "terminal_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "terminal_code": row.terminal_code,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/tickets/{ticket_id}", tags=["Tickets"])
def get_ticket(ticket_id: int, db: Session = Depends(get_db)) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket not found")
    return {
        "data": {
            "ticket_id": ticket.id,
            "tenant_id": ticket.tenant_id,
            "location_id": ticket.location_id,
            "business_day_id": ticket.business_day_id,
            "source_system_id": ticket.source_system_id,
            "external_ticket_id": ticket.external_ticket_id,
            "opened_at": ticket.opened_at.isoformat(),
            "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
            "channel_id": ticket.channel_id,
            "order_type_raw": ticket.order_type_raw,
            "covers": ticket.covers,
            "gross_amount": float(ticket.gross_amount),
            "discount_amount": float(ticket.discount_amount),
            "net_amount": float(ticket.net_amount),
            "tax_amount": float(ticket.tax_amount),
            "cashier_employee_id": ticket.cashier_employee_id,
            "terminal_id": ticket.terminal_id,
            "status": ticket.status,
            "metadata": ticket.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets", tags=["Tickets"])
def list_tickets(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    business_day_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    source_system_id: Optional[int] = Query(default=None),
    channel_id: Optional[int] = Query(default=None),
    opened_from: Optional[datetime] = Query(default=None),
    opened_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Ticket)
    if tenant_id is not None:
        query = query.filter(Ticket.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Ticket.location_id == location_id)
    if business_day_id is not None:
        query = query.filter(Ticket.business_day_id == business_day_id)
    if status is not None:
        query = query.filter(Ticket.status == status)
    if source_system_id is not None:
        query = query.filter(Ticket.source_system_id == source_system_id)
    if channel_id is not None:
        query = query.filter(Ticket.channel_id == channel_id)
    if opened_from is not None:
        query = query.filter(Ticket.opened_at >= opened_from)
    if opened_to is not None:
        query = query.filter(Ticket.opened_at <= opened_to)
    rows, next_cursor = _paginate_by_id(query, Ticket, limit, cursor)
    data = [
        {
            "ticket_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "business_day_id": row.business_day_id,
            "source_system_id": row.source_system_id,
            "external_ticket_id": row.external_ticket_id,
            "opened_at": row.opened_at.isoformat(),
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "channel_id": row.channel_id,
            "order_type_raw": row.order_type_raw,
            "covers": row.covers,
            "gross_amount": float(row.gross_amount),
            "discount_amount": float(row.discount_amount),
            "net_amount": float(row.net_amount),
            "tax_amount": float(row.tax_amount),
            "cashier_employee_id": row.cashier_employee_id,
            "terminal_id": row.terminal_id,
            "status": row.status,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-line-items/{ticket_line_item_id}", tags=["Ticket Line Items"])
def get_ticket_line_item(ticket_line_item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(TicketLineItem, ticket_line_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ticket line item not found")
    return {
        "data": {
            "ticket_line_item_id": item.id,
            "tenant_id": item.tenant_id,
            "ticket_id": item.ticket_id,
            "item_id": item.item_id,
            "item_name_raw": item.item_name_raw,
            "qty": float(item.qty),
            "uom": item.uom,
            "unit_price": float(item.unit_price) if item.unit_price is not None else None,
            "gross_line_amount": float(item.gross_line_amount),
            "discount_line_amount": float(item.discount_line_amount),
            "net_line_amount": float(item.net_line_amount),
            "tax_line_amount": float(item.tax_line_amount),
            "channel_id": item.channel_id,
            "metadata": item.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets/{ticket_id}/line-items", tags=["Ticket Line Items"])
def list_ticket_line_items_for_ticket(
    ticket_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=500, ge=1, le=1000),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketLineItem).filter(
        TicketLineItem.tenant_id == tenant_id,
        TicketLineItem.ticket_id == ticket_id,
    )
    rows, next_cursor = _paginate_by_id(query, TicketLineItem, limit, cursor)
    data = [
        {
            "ticket_line_item_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "item_id": row.item_id,
            "item_name_raw": row.item_name_raw,
            "qty": float(row.qty),
            "uom": row.uom,
            "unit_price": float(row.unit_price) if row.unit_price is not None else None,
            "gross_line_amount": float(row.gross_line_amount),
            "discount_line_amount": float(row.discount_line_amount),
            "net_line_amount": float(row.net_line_amount),
            "tax_line_amount": float(row.tax_line_amount),
            "channel_id": row.channel_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-line-items", tags=["Ticket Line Items"])
def list_ticket_line_items(
    tenant_id: Optional[int] = Query(default=None),
    ticket_id: Optional[int] = Query(default=None),
    item_id: Optional[int] = Query(default=None),
    channel_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketLineItem)
    if tenant_id is not None:
        query = query.filter(TicketLineItem.tenant_id == tenant_id)
    if ticket_id is not None:
        query = query.filter(TicketLineItem.ticket_id == ticket_id)
    if item_id is not None:
        query = query.filter(TicketLineItem.item_id == item_id)
    if channel_id is not None:
        query = query.filter(TicketLineItem.channel_id == channel_id)
    rows, next_cursor = _paginate_by_id(query, TicketLineItem, limit, cursor)
    data = [
        {
            "ticket_line_item_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "item_id": row.item_id,
            "item_name_raw": row.item_name_raw,
            "qty": float(row.qty),
            "uom": row.uom,
            "unit_price": float(row.unit_price) if row.unit_price is not None else None,
            "gross_line_amount": float(row.gross_line_amount),
            "discount_line_amount": float(row.discount_line_amount),
            "net_line_amount": float(row.net_line_amount),
            "tax_line_amount": float(row.tax_line_amount),
            "channel_id": row.channel_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-payments/{ticket_payment_id}", tags=["Ticket Payments"])
def get_ticket_payment(ticket_payment_id: int, db: Session = Depends(get_db)) -> dict:
    payment = db.get(TicketPayment, ticket_payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="ticket payment not found")
    return {
        "data": {
            "ticket_payment_id": payment.id,
            "tenant_id": payment.tenant_id,
            "ticket_id": payment.ticket_id,
            "paid_at": payment.paid_at.isoformat(),
            "tender_type": payment.tender_type,
            "amount": float(payment.amount),
            "card_brand": payment.card_brand,
            "last4": payment.last4,
            "metadata": payment.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets/{ticket_id}/payments", tags=["Ticket Payments"])
def list_ticket_payments_for_ticket(
    ticket_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketPayment).filter(
        TicketPayment.tenant_id == tenant_id,
        TicketPayment.ticket_id == ticket_id,
    )
    rows, next_cursor = _paginate_by_id(query, TicketPayment, limit, cursor)
    data = [
        {
            "ticket_payment_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "paid_at": row.paid_at.isoformat(),
            "tender_type": row.tender_type,
            "amount": float(row.amount),
            "card_brand": row.card_brand,
            "last4": row.last4,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-payments", tags=["Ticket Payments"])
def list_ticket_payments(
    tenant_id: Optional[int] = Query(default=None),
    tender_type: Optional[str] = Query(default=None),
    paid_from: Optional[datetime] = Query(default=None),
    paid_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketPayment)
    if tenant_id is not None:
        query = query.filter(TicketPayment.tenant_id == tenant_id)
    if tender_type is not None:
        query = query.filter(TicketPayment.tender_type == tender_type)
    if paid_from is not None:
        query = query.filter(TicketPayment.paid_at >= paid_from)
    if paid_to is not None:
        query = query.filter(TicketPayment.paid_at <= paid_to)
    rows, next_cursor = _paginate_by_id(query, TicketPayment, limit, cursor)
    data = [
        {
            "ticket_payment_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "paid_at": row.paid_at.isoformat(),
            "tender_type": row.tender_type,
            "amount": float(row.amount),
            "card_brand": row.card_brand,
            "last4": row.last4,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-discounts/{ticket_discount_id}", tags=["Ticket Discounts"])
def get_ticket_discount(ticket_discount_id: int, db: Session = Depends(get_db)) -> dict:
    discount = db.get(TicketDiscount, ticket_discount_id)
    if not discount:
        raise HTTPException(status_code=404, detail="ticket discount not found")
    return {
        "data": {
            "ticket_discount_id": discount.id,
            "tenant_id": discount.tenant_id,
            "ticket_id": discount.ticket_id,
            "amount": float(discount.amount),
            "reason": discount.reason,
            "applied_at": discount.applied_at.isoformat() if discount.applied_at else None,
            "metadata": discount.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets/{ticket_id}/discounts", tags=["Ticket Discounts"])
def list_ticket_discounts_for_ticket(
    ticket_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketDiscount).filter(
        TicketDiscount.tenant_id == tenant_id,
        TicketDiscount.ticket_id == ticket_id,
    )
    rows, next_cursor = _paginate_by_id(query, TicketDiscount, limit, cursor)
    data = [
        {
            "ticket_discount_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-discounts", tags=["Ticket Discounts"])
def list_ticket_discounts(
    tenant_id: Optional[int] = Query(default=None),
    ticket_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketDiscount)
    if tenant_id is not None:
        query = query.filter(TicketDiscount.tenant_id == tenant_id)
    if ticket_id is not None:
        query = query.filter(TicketDiscount.ticket_id == ticket_id)
    rows, next_cursor = _paginate_by_id(query, TicketDiscount, limit, cursor)
    data = [
        {
            "ticket_discount_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-voids/{ticket_void_id}", tags=["Ticket Voids"])
def get_ticket_void(ticket_void_id: int, db: Session = Depends(get_db)) -> dict:
    void = db.get(TicketVoid, ticket_void_id)
    if not void:
        raise HTTPException(status_code=404, detail="ticket void not found")
    return {
        "data": {
            "ticket_void_id": void.id,
            "tenant_id": void.tenant_id,
            "ticket_id": void.ticket_id,
            "amount": float(void.amount),
            "reason": void.reason,
            "voided_at": void.voided_at.isoformat(),
            "cashier_employee_id": void.cashier_employee_id,
            "terminal_id": void.terminal_id,
            "metadata": void.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets/{ticket_id}/voids", tags=["Ticket Voids"])
def list_ticket_voids_for_ticket(
    ticket_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketVoid).filter(
        TicketVoid.tenant_id == tenant_id,
        TicketVoid.ticket_id == ticket_id,
    )
    rows, next_cursor = _paginate_by_id(query, TicketVoid, limit, cursor)
    data = [
        {
            "ticket_void_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "voided_at": row.voided_at.isoformat(),
            "cashier_employee_id": row.cashier_employee_id,
            "terminal_id": row.terminal_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-voids", tags=["Ticket Voids"])
def list_ticket_voids(
    tenant_id: Optional[int] = Query(default=None),
    cashier_employee_id: Optional[int] = Query(default=None),
    voided_from: Optional[datetime] = Query(default=None),
    voided_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketVoid)
    if tenant_id is not None:
        query = query.filter(TicketVoid.tenant_id == tenant_id)
    if cashier_employee_id is not None:
        query = query.filter(TicketVoid.cashier_employee_id == cashier_employee_id)
    if voided_from is not None:
        query = query.filter(TicketVoid.voided_at >= voided_from)
    if voided_to is not None:
        query = query.filter(TicketVoid.voided_at <= voided_to)
    rows, next_cursor = _paginate_by_id(query, TicketVoid, limit, cursor)
    data = [
        {
            "ticket_void_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "voided_at": row.voided_at.isoformat(),
            "cashier_employee_id": row.cashier_employee_id,
            "terminal_id": row.terminal_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-refunds/{ticket_refund_id}", tags=["Ticket Refunds"])
def get_ticket_refund(ticket_refund_id: int, db: Session = Depends(get_db)) -> dict:
    refund = db.get(TicketRefund, ticket_refund_id)
    if not refund:
        raise HTTPException(status_code=404, detail="ticket refund not found")
    return {
        "data": {
            "ticket_refund_id": refund.id,
            "tenant_id": refund.tenant_id,
            "ticket_id": refund.ticket_id,
            "amount": float(refund.amount),
            "reason": refund.reason,
            "refunded_at": refund.refunded_at.isoformat(),
            "payment_method": refund.payment_method,
            "metadata": refund.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/tickets/{ticket_id}/refunds", tags=["Ticket Refunds"])
def list_ticket_refunds_for_ticket(
    ticket_id: int,
    tenant_id: int = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketRefund).filter(
        TicketRefund.tenant_id == tenant_id,
        TicketRefund.ticket_id == ticket_id,
    )
    rows, next_cursor = _paginate_by_id(query, TicketRefund, limit, cursor)
    data = [
        {
            "ticket_refund_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "refunded_at": row.refunded_at.isoformat(),
            "payment_method": row.payment_method,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/ticket-refunds", tags=["Ticket Refunds"])
def list_ticket_refunds(
    tenant_id: Optional[int] = Query(default=None),
    refunded_from: Optional[datetime] = Query(default=None),
    refunded_to: Optional[datetime] = Query(default=None),
    payment_method: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(TicketRefund)
    if tenant_id is not None:
        query = query.filter(TicketRefund.tenant_id == tenant_id)
    if refunded_from is not None:
        query = query.filter(TicketRefund.refunded_at >= refunded_from)
    if refunded_to is not None:
        query = query.filter(TicketRefund.refunded_at <= refunded_to)
    if payment_method is not None:
        query = query.filter(TicketRefund.payment_method == payment_method)
    rows, next_cursor = _paginate_by_id(query, TicketRefund, limit, cursor)
    data = [
        {
            "ticket_refund_id": row.id,
            "tenant_id": row.tenant_id,
            "ticket_id": row.ticket_id,
            "amount": float(row.amount),
            "reason": row.reason,
            "refunded_at": row.refunded_at.isoformat(),
            "payment_method": row.payment_method,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/cash-counts/{cash_count_id}", tags=["Cash Counts"])
def get_cash_count(cash_count_id: int, db: Session = Depends(get_db)) -> dict:
    cash_count = db.get(CashCount, cash_count_id)
    if not cash_count:
        raise HTTPException(status_code=404, detail="cash count not found")
    return {
        "data": {
            "cash_count_id": cash_count.id,
            "tenant_id": cash_count.tenant_id,
            "location_id": cash_count.location_id,
            "business_day_id": cash_count.business_day_id,
            "counted_at": cash_count.counted_at.isoformat(),
            "expected_cash": float(cash_count.expected_cash or 0),
            "counted_cash": float(cash_count.counted_cash or 0),
            "variance": float(cash_count.variance or 0),
            "cashier_employee_id": cash_count.cashier_employee_id,
            "metadata": cash_count.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/cash-counts", tags=["Cash Counts"])
def list_cash_counts(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    business_day_id: Optional[int] = Query(default=None),
    counted_from: Optional[datetime] = Query(default=None),
    counted_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(CashCount)
    if tenant_id is not None:
        query = query.filter(CashCount.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(CashCount.location_id == location_id)
    if business_day_id is not None:
        query = query.filter(CashCount.business_day_id == business_day_id)
    if counted_from is not None:
        query = query.filter(CashCount.counted_at >= counted_from)
    if counted_to is not None:
        query = query.filter(CashCount.counted_at <= counted_to)
    rows, next_cursor = _paginate_by_id(query, CashCount, limit, cursor)
    data = [
        {
            "cash_count_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "business_day_id": row.business_day_id,
            "counted_at": row.counted_at.isoformat(),
            "expected_cash": float(row.expected_cash or 0),
            "counted_cash": float(row.counted_cash or 0),
            "variance": float(row.variance or 0),
            "cashier_employee_id": row.cashier_employee_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/payouts/{payout_id}", tags=["Payouts"])
def get_payout(payout_id: int, db: Session = Depends(get_db)) -> dict:
    payout = db.get(Payout, payout_id)
    if not payout:
        raise HTTPException(status_code=404, detail="payout not found")
    return {
        "data": {
            "payout_id": payout.id,
            "tenant_id": payout.tenant_id,
            "location_id": payout.location_id,
            "source_system_id": payout.source_system_id,
            "provider": payout.provider,
            "payout_reference": payout.payout_reference,
            "status": payout.status,
            "amount": float(payout.amount),
            "currency_code": payout.currency_code,
            "period_start": payout.period_start.isoformat() if payout.period_start else None,
            "period_end": payout.period_end.isoformat() if payout.period_end else None,
            "expected_payout_at": payout.expected_payout_at.isoformat()
            if payout.expected_payout_at
            else None,
            "paid_at": payout.paid_at.isoformat() if payout.paid_at else None,
            "created_at": payout.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/payouts", tags=["Payouts"])
def list_payouts(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    expected_from: Optional[datetime] = Query(default=None),
    expected_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Payout)
    if tenant_id is not None:
        query = query.filter(Payout.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Payout.location_id == location_id)
    if status is not None:
        query = query.filter(Payout.status == status)
    if provider is not None:
        query = query.filter(Payout.provider == provider)
    if expected_from is not None:
        query = query.filter(Payout.expected_payout_at >= expected_from)
    if expected_to is not None:
        query = query.filter(Payout.expected_payout_at <= expected_to)
    rows, next_cursor = _paginate_by_id(query, Payout, limit, cursor)
    data = [
        {
            "payout_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "source_system_id": row.source_system_id,
            "provider": row.provider,
            "payout_reference": row.payout_reference,
            "status": row.status,
            "amount": float(row.amount),
            "currency_code": row.currency_code,
            "period_start": row.period_start.isoformat() if row.period_start else None,
            "period_end": row.period_end.isoformat() if row.period_end else None,
            "expected_payout_at": row.expected_payout_at.isoformat()
            if row.expected_payout_at
            else None,
            "paid_at": row.paid_at.isoformat() if row.paid_at else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/stockout-events/{stockout_event_id}", tags=["Stockout Events"])
def get_stockout_event(stockout_event_id: int, db: Session = Depends(get_db)) -> dict:
    event = db.get(StockoutEvent, stockout_event_id)
    if not event:
        raise HTTPException(status_code=404, detail="stockout event not found")
    return {
        "data": {
            "stockout_event_id": event.id,
            "tenant_id": event.tenant_id,
            "location_id": event.location_id,
            "item_id": event.item_id,
            "item_name_raw": event.item_name_raw,
            "started_at": event.started_at.isoformat(),
            "ended_at": event.ended_at.isoformat() if event.ended_at else None,
            "reason": event.reason,
            "source_system_id": event.source_system_id,
            "metadata": event.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/stockout-events", tags=["Stockout Events"])
def list_stockout_events(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    item_id: Optional[int] = Query(default=None),
    active_at: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(StockoutEvent)
    if tenant_id is not None:
        query = query.filter(StockoutEvent.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(StockoutEvent.location_id == location_id)
    if item_id is not None:
        query = query.filter(StockoutEvent.item_id == item_id)
    if active_at is not None:
        query = query.filter(StockoutEvent.started_at <= active_at).filter(
            (StockoutEvent.ended_at.is_(None)) | (StockoutEvent.ended_at >= active_at)
        )
    rows, next_cursor = _paginate_by_id(query, StockoutEvent, limit, cursor)
    data = [
        {
            "stockout_event_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "item_id": row.item_id,
            "item_name_raw": row.item_name_raw,
            "started_at": row.started_at.isoformat(),
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "reason": row.reason,
            "source_system_id": row.source_system_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/assets/{asset_id}", tags=["Assets"])
def get_asset(asset_id: int, db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="asset not found")
    return {
        "data": {
            "asset_id": asset.id,
            "tenant_id": asset.tenant_id,
            "location_id": asset.location_id,
            "asset_type": asset.asset_type,
            "asset_name": asset.asset_name,
            "serial_number": asset.serial_number,
            "is_critical": asset.is_critical,
            "is_active": asset.is_active,
        },
        "meta": _meta(),
    }


@app.get("/api/v1/assets", tags=["Assets"])
def list_assets(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    is_critical: Optional[bool] = Query(default=None),
    asset_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Asset)
    if tenant_id is not None:
        query = query.filter(Asset.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Asset.location_id == location_id)
    if is_active is not None:
        query = query.filter(Asset.is_active == is_active)
    if is_critical is not None:
        query = query.filter(Asset.is_critical == is_critical)
    if asset_type is not None:
        query = query.filter(Asset.asset_type == asset_type)
    rows, next_cursor = _paginate_by_id(query, Asset, limit, cursor)
    data = [
        {
            "asset_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "asset_type": row.asset_type,
            "asset_name": row.asset_name,
            "serial_number": row.serial_number,
            "is_critical": row.is_critical,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/incidents/{incident_id}", tags=["Incidents"])
def get_incident(incident_id: int, db: Session = Depends(get_db)) -> dict:
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")
    return {
        "data": {
            "incident_id": incident.id,
            "tenant_id": incident.tenant_id,
            "location_id": incident.location_id,
            "asset_id": incident.asset_id,
            "status": incident.status,
            "incident_type": incident.incident_type,
            "opened_at": incident.opened_at.isoformat(),
            "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
            "title": incident.title,
            "description": incident.description,
            "source_system_id": incident.source_system_id,
            "metadata": incident.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/incidents", tags=["Incidents"])
def list_incidents(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    asset_id: Optional[int] = Query(default=None),
    opened_from: Optional[datetime] = Query(default=None),
    opened_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Incident)
    if tenant_id is not None:
        query = query.filter(Incident.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Incident.location_id == location_id)
    if status is not None:
        query = query.filter(Incident.status == status)
    if asset_id is not None:
        query = query.filter(Incident.asset_id == asset_id)
    if opened_from is not None:
        query = query.filter(Incident.opened_at >= opened_from)
    if opened_to is not None:
        query = query.filter(Incident.opened_at <= opened_to)
    rows, next_cursor = _paginate_by_id(query, Incident, limit, cursor)
    data = [
        {
            "incident_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "asset_id": row.asset_id,
            "status": row.status,
            "incident_type": row.incident_type,
            "opened_at": row.opened_at.isoformat(),
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "title": row.title,
            "description": row.description,
            "source_system_id": row.source_system_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/work-orders/{work_order_id}", tags=["Work Orders"])
def get_work_order(work_order_id: int, db: Session = Depends(get_db)) -> dict:
    work_order = db.get(WorkOrder, work_order_id)
    if not work_order:
        raise HTTPException(status_code=404, detail="work order not found")
    return {
        "data": {
            "work_order_id": work_order.id,
            "tenant_id": work_order.tenant_id,
            "location_id": work_order.location_id,
            "incident_id": work_order.incident_id,
            "asset_id": work_order.asset_id,
            "status": work_order.status,
            "created_at": work_order.created_at.isoformat(),
            "due_at": work_order.due_at.isoformat() if work_order.due_at else None,
            "completed_at": work_order.completed_at.isoformat() if work_order.completed_at else None,
            "description": work_order.description,
            "metadata": work_order.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/work-orders", tags=["Work Orders"])
def list_work_orders(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    incident_id: Optional[int] = Query(default=None),
    asset_id: Optional[int] = Query(default=None),
    due_from: Optional[datetime] = Query(default=None),
    due_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(WorkOrder)
    if tenant_id is not None:
        query = query.filter(WorkOrder.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(WorkOrder.location_id == location_id)
    if status is not None:
        query = query.filter(WorkOrder.status == status)
    if incident_id is not None:
        query = query.filter(WorkOrder.incident_id == incident_id)
    if asset_id is not None:
        query = query.filter(WorkOrder.asset_id == asset_id)
    if due_from is not None:
        query = query.filter(WorkOrder.due_at >= due_from)
    if due_to is not None:
        query = query.filter(WorkOrder.due_at <= due_to)
    rows, next_cursor = _paginate_by_id(query, WorkOrder, limit, cursor)
    data = [
        {
            "work_order_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "incident_id": row.incident_id,
            "asset_id": row.asset_id,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "description": row.description,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/open-close-signals/{open_close_signal_id}", tags=["Open/Close Signals"])
def get_open_close_signal(open_close_signal_id: int, db: Session = Depends(get_db)) -> dict:
    signal = db.get(OpenCloseSignal, open_close_signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="open/close signal not found")
    return {
        "data": {
            "open_close_signal_id": signal.id,
            "tenant_id": signal.tenant_id,
            "location_id": signal.location_id,
            "business_day_id": signal.business_day_id,
            "signal_type": signal.signal_type,
            "occurred_at": signal.occurred_at.isoformat(),
            "source_system_id": signal.source_system_id,
            "metadata": signal.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/open-close-signals", tags=["Open/Close Signals"])
def list_open_close_signals(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    business_day_id: Optional[int] = Query(default=None),
    signal_type: Optional[str] = Query(default=None),
    occurred_from: Optional[datetime] = Query(default=None),
    occurred_to: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(OpenCloseSignal)
    if tenant_id is not None:
        query = query.filter(OpenCloseSignal.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(OpenCloseSignal.location_id == location_id)
    if business_day_id is not None:
        query = query.filter(OpenCloseSignal.business_day_id == business_day_id)
    if signal_type is not None:
        query = query.filter(OpenCloseSignal.signal_type == signal_type)
    if occurred_from is not None:
        query = query.filter(OpenCloseSignal.occurred_at >= occurred_from)
    if occurred_to is not None:
        query = query.filter(OpenCloseSignal.occurred_at <= occurred_to)
    rows, next_cursor = _paginate_by_id(query, OpenCloseSignal, limit, cursor)
    data = [
        {
            "open_close_signal_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "business_day_id": row.business_day_id,
            "signal_type": row.signal_type,
            "occurred_at": row.occurred_at.isoformat(),
            "source_system_id": row.source_system_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/pos-downtime-events/{pos_downtime_event_id}", tags=["POS Downtime Events"])
def get_pos_downtime_event(pos_downtime_event_id: int, db: Session = Depends(get_db)) -> dict:
    event = db.get(PosDowntimeEvent, pos_downtime_event_id)
    if not event:
        raise HTTPException(status_code=404, detail="pos downtime event not found")
    return {
        "data": {
            "pos_downtime_event_id": event.id,
            "tenant_id": event.tenant_id,
            "location_id": event.location_id,
            "started_at": event.started_at.isoformat(),
            "ended_at": event.ended_at.isoformat() if event.ended_at else None,
            "reason": event.reason,
            "source_system_id": event.source_system_id,
            "metadata": event.metadata_json or {},
        },
        "meta": _meta(),
    }


@app.get("/api/v1/pos-downtime-events", tags=["POS Downtime Events"])
def list_pos_downtime_events(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    active_at: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(PosDowntimeEvent)
    if tenant_id is not None:
        query = query.filter(PosDowntimeEvent.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(PosDowntimeEvent.location_id == location_id)
    if active_at is not None:
        query = query.filter(PosDowntimeEvent.started_at <= active_at).filter(
            (PosDowntimeEvent.ended_at.is_(None)) | (PosDowntimeEvent.ended_at >= active_at)
        )
    rows, next_cursor = _paginate_by_id(query, PosDowntimeEvent, limit, cursor)
    data = [
        {
            "pos_downtime_event_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "started_at": row.started_at.isoformat(),
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "reason": row.reason,
            "source_system_id": row.source_system_id,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/policies/{policy_id}", tags=["Policies"])
def get_policy(policy_id: int, db: Session = Depends(get_db)) -> dict:
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy not found")
    return {
        "data": {
            "policy_id": policy.id,
            "tenant_id": policy.tenant_id,
            "policy_version": policy.policy_version,
            "effective_from": policy.effective_from.isoformat(),
            "config": policy.config,
            "created_at": policy.created_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/policies", tags=["Policies"])
def list_policies(
    tenant_id: Optional[int] = Query(default=None),
    active_at: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Policy)
    if tenant_id is not None:
        query = query.filter(Policy.tenant_id == tenant_id)
    if active_at is not None:
        query = query.filter(Policy.effective_from <= active_at)
    rows, next_cursor = _paginate_by_id(query, Policy, limit, cursor)
    data = [
        {
            "policy_id": row.id,
            "tenant_id": row.tenant_id,
            "policy_version": row.policy_version,
            "effective_from": row.effective_from.isoformat(),
            "config": row.config,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/snapshots", tags=["Snapshots"])
def list_snapshots(
    tenant_id: Optional[int] = Query(default=None),
    location_id: Optional[int] = Query(default=None),
    business_day_id: Optional[int] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Snapshot)
    if tenant_id is not None:
        query = query.filter(Snapshot.tenant_id == tenant_id)
    if location_id is not None:
        query = query.filter(Snapshot.location_id == location_id)
    if business_day_id is not None:
        query = query.filter(Snapshot.business_day_id == business_day_id)
    if from_date is not None or to_date is not None:
        query = query.join(BusinessDay, Snapshot.business_day_id == BusinessDay.id)
        if from_date is not None:
            query = query.filter(BusinessDay.business_date >= from_date)
        if to_date is not None:
            query = query.filter(BusinessDay.business_date <= to_date)
    rows, next_cursor = _paginate_by_id(query, Snapshot, limit, cursor)
    data = [
        {
            "snapshot_id": row.id,
            "tenant_id": row.tenant_id,
            "location_id": row.location_id,
            "business_day_id": row.business_day_id,
            "numbers_status": (row.snapshot_metadata or {}).get("numbers_status"),
            "feed_completeness_flags": (row.snapshot_metadata or {}).get(
                "feed_completeness_flags"
            ),
            "snapshot_metadata": row.snapshot_metadata or {},
            "day_state_object": row.day_state_object,
            "summary_hash": row.summary_hash,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/report-runs/{report_run_id}", tags=["Report Runs"])
def get_report_run(report_run_id: int, db: Session = Depends(get_db)) -> dict:
    report_run = db.get(ReportRun, report_run_id)
    if not report_run:
        raise HTTPException(status_code=404, detail="report run not found")
    return {
        "data": {
            "report_run_id": report_run.id,
            "tenant_id": report_run.tenant_id,
            "snapshot_id": report_run.snapshot_id,
            "policy_version": (report_run.metadata_json or {}).get("policy_version"),
            "report_version": report_run.report_version,
            "status": "COMPLETED",
            "outputs": {
                "truth_labels": report_run.truth_labels or {},
                "kpis": report_run.kpis or {},
                "alerts": report_run.alerts or [],
                "ops_issues": report_run.ops_issues or [],
                "recommendations": report_run.recommendations or [],
                "render_manifest": (report_run.metadata_json or {}).get(
                    "render_manifest", {}
                ),
            },
            "created_at": report_run.generated_at.isoformat(),
        },
        "meta": _meta(),
    }


@app.get("/api/v1/report-runs", tags=["Report Runs"])
def list_report_runs(
    tenant_id: Optional[int] = Query(default=None),
    snapshot_id: Optional[int] = Query(default=None),
    report_version: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ReportRun)
    if tenant_id is not None:
        query = query.filter(ReportRun.tenant_id == tenant_id)
    if snapshot_id is not None:
        query = query.filter(ReportRun.snapshot_id == snapshot_id)
    if report_version is not None:
        query = query.filter(ReportRun.report_version == report_version)
    rows, next_cursor = _paginate_by_id(query, ReportRun, limit, cursor)
    data = [
        {
            "report_run_id": row.id,
            "tenant_id": row.tenant_id,
            "snapshot_id": row.snapshot_id,
            "policy_version": (row.metadata_json or {}).get("policy_version"),
            "report_version": row.report_version,
            "status": "COMPLETED",
            "outputs": {
                "truth_labels": row.truth_labels or {},
                "kpis": row.kpis or {},
                "alerts": row.alerts or [],
                "ops_issues": row.ops_issues or [],
                "recommendations": row.recommendations or [],
                "render_manifest": (row.metadata_json or {}).get(
                    "render_manifest", {}
                ),
            },
            "created_at": row.generated_at.isoformat(),
        }
        for row in rows
    ]
    return {"data": data, "meta": _list_meta(limit, cursor, next_cursor)}


@app.get("/api/v1/daily-state", tags=["Daily State"])
def get_daily_state(
    tenant_id: int = Query(...),
    location_id: int = Query(...),
    business_date: date = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    day = db.query(BusinessDay).filter(
        BusinessDay.tenant_id == tenant_id,
        BusinessDay.location_id == location_id,
        BusinessDay.business_date == business_date,
    ).first()
    snapshot = None
    report_run = None
    if day:
        snapshot = db.query(Snapshot).filter(
            Snapshot.tenant_id == tenant_id,
            Snapshot.location_id == location_id,
            Snapshot.business_day_id == day.id,
        ).order_by(Snapshot.id.desc()).first()
        if snapshot:
            report_run = db.query(ReportRun).filter(
                ReportRun.tenant_id == tenant_id,
                ReportRun.snapshot_id == snapshot.id,
            ).order_by(ReportRun.id.desc()).first()
    return {
        "data": {
            "tenant_id": tenant_id,
            "location_id": location_id,
            "business_date": business_date.isoformat(),
            "business_day_id": day.id if day else None,
            "snapshot_id": snapshot.id if snapshot else None,
            "report_run_id": report_run.id if report_run else None,
            "numbers_status": (snapshot.snapshot_metadata or {}).get("numbers_status")
            if snapshot
            else None,
            "feed_completeness_flags": (snapshot.snapshot_metadata or {}).get(
                "feed_completeness_flags"
            )
            if snapshot
            else None,
            "day_state_object": snapshot.day_state_object if snapshot else None,
            "report_outputs": {
                "truth_labels": report_run.truth_labels or {},
                "kpis": report_run.kpis or {},
                "alerts": report_run.alerts or [],
                "ops_issues": report_run.ops_issues or [],
                "recommendations": report_run.recommendations or [],
            }
            if report_run
            else None,
            "generated_at": _now().isoformat(),
        },
        "meta": _meta(),
    }


class LedgerEvent(BaseModel):
    source_event_id: str
    event_time: datetime
    payload: dict

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_event_id": "pos_ticket_98765",
                "event_time": "2026-01-13T13:40:00+05:00",
                "payload": {},
            }
        }
    }


class LedgerAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[LedgerEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system_id": 1,
                "business_day_id": 1,
                "events": [
                    {
                        "source_event_id": "pos_ticket_98765",
                        "event_time": "2026-01-13T13:40:00+05:00",
                        "payload": {},
                    }
                ],
            }
        }
    }


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/{ledger_name}:append",
    tags=["ERP Ledgers"],
)
def append_ledger_events(
    tenant_id: int,
    location_id: int,
    ledger_name: str,
    payload: LedgerAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    feed_type = ERP_LEDGER_FEED_TYPES.get(ledger_name, ledger_name.upper())
    result = _append_ledger_events(
        db=db,
        tenant_id=tenant_id,
        location_id=location_id,
        feed_type=feed_type,
        domain="ERP",
        source_system_id=payload.source_system_id,
        business_day_id=payload.business_day_id,
        events=[event.model_dump() for event in payload.events],
    )
    return result


class SalesEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    gross_amount_minor: int
    discount_amount_minor: int
    net_amount_minor: int
    tax_amount_minor: int
    currency_code: str
    event_time: Optional[datetime] = None


class SalesAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    sales: list[SalesEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system_id": 1,
                "business_day_id": 1,
                "sales": [
                    {
                        "source_event_id": "ticket_101",
                        "ticket_id_external": "101",
                        "opened_at": "2026-01-13T13:05:00+05:00",
                        "closed_at": "2026-01-13T13:42:00+05:00",
                        "gross_amount_minor": 240000,
                        "discount_amount_minor": 10000,
                        "net_amount_minor": 230000,
                        "tax_amount_minor": 0,
                        "currency_code": "PKR",
                    }
                ],
            }
        }
    }


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/sales:append",
    tags=["ERP Ledgers"],
)
def append_sales(
    tenant_id: int,
    location_id: int,
    payload: SalesAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.sales]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["sales"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class OrdersTicketsEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    channel_code_raw: Optional[str] = None
    covers: Optional[int] = None
    items_count: Optional[int] = None
    terminal_code: Optional[str] = None
    cashier_external_key: Optional[str] = None
    event_time: Optional[datetime] = None


class OrdersTicketsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    tickets: list[OrdersTicketsEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system_id": 1,
                "business_day_id": 1,
                "tickets": [
                    {
                        "source_event_id": "ticket_101",
                        "ticket_id_external": "101",
                        "opened_at": "2026-01-13T13:05:00+05:00",
                        "closed_at": "2026-01-13T13:42:00+05:00",
                        "channel_code_raw": "DINEIN",
                        "covers": 2,
                        "items_count": 5,
                        "terminal_code": "T1",
                        "cashier_external_key": "emp_9",
                    }
                ],
            }
        }
    }


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/orders-tickets:append",
    tags=["ERP Ledgers"],
)
def append_orders_tickets(
    tenant_id: int,
    location_id: int,
    payload: OrdersTicketsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.tickets]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["orders-tickets"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class DiscountEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    applied_at: Optional[datetime] = None
    amount_minor: int
    reason: Optional[str] = None
    event_time: Optional[datetime] = None


class DiscountsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    discounts: list[DiscountEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system_id": 1,
                "business_day_id": 1,
                "discounts": [
                    {
                        "source_event_id": "disc_555",
                        "ticket_id_external": "101",
                        "applied_at": "2026-01-13T13:30:00+05:00",
                        "amount_minor": 10000,
                        "reason": "PROMO10",
                    }
                ],
            }
        }
    }


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/discounts:append",
    tags=["ERP Ledgers"],
)
def append_discounts(
    tenant_id: int,
    location_id: int,
    payload: DiscountsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.discounts]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["discounts"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class VoidEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    voided_at: Optional[datetime] = None
    amount_minor: int
    reason: Optional[str] = None
    event_time: Optional[datetime] = None


class VoidsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    voids: list[VoidEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/voids:append",
    tags=["ERP Ledgers"],
)
def append_voids(
    tenant_id: int,
    location_id: int,
    payload: VoidsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.voids]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["voids"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class RefundEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    refunded_at: Optional[datetime] = None
    amount_minor: int
    reason: Optional[str] = None
    event_time: Optional[datetime] = None


class RefundsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    refunds: list[RefundEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/refunds:append",
    tags=["ERP Ledgers"],
)
def append_refunds(
    tenant_id: int,
    location_id: int,
    payload: RefundsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.refunds]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["refunds"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class OpenCloseDowntimeEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    type: str
    meta: Optional[dict] = None


class OpenCloseDowntimeAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[OpenCloseDowntimeEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/open-close-downtime:append",
    tags=["ERP Ledgers"],
)
def append_open_close_downtime(
    tenant_id: int,
    location_id: int,
    payload: OpenCloseDowntimeAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.events]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["open-close-downtime"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class PaymentTenderEvent(BaseModel):
    source_event_id: str
    ticket_id_external: str
    paid_at: Optional[datetime] = None
    tender_type: str
    amount_minor: int
    card_brand: Optional[str] = None
    last4: Optional[str] = None
    event_time: Optional[datetime] = None


class PaymentsTenderAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    payments: list[PaymentTenderEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/payments-tender:append",
    tags=["ERP Ledgers"],
)
def append_payments_tender(
    tenant_id: int,
    location_id: int,
    payload: PaymentsTenderAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.payments]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["payments-tender"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class PayoutLedgerEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    amount_minor: int
    currency_code: str
    status: Optional[str] = None
    payout_reference: Optional[str] = None


class PayoutLedgerAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    payouts: list[PayoutLedgerEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/payouts:append",
    tags=["ERP Ledgers"],
)
def append_payouts_ledger(
    tenant_id: int,
    location_id: int,
    payload: PayoutLedgerAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.payouts]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["payouts"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class CashVarianceEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    expected_cash_minor: int
    counted_cash_minor: int
    variance_minor: int
    cashier_external_key: Optional[str] = None


class CashVarianceAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    cash_variances: list[CashVarianceEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/cash-variance:append",
    tags=["ERP Ledgers"],
)
def append_cash_variance(
    tenant_id: int,
    location_id: int,
    payload: CashVarianceAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.cash_variances]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["cash-variance"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class LaborLedgerEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    employee_external_key: Optional[str] = None
    role: Optional[str] = None
    clock_in: Optional[datetime] = None
    clock_out: Optional[datetime] = None
    hourly_rate_minor: Optional[int] = None


class LaborLedgerAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    labor: list[LaborLedgerEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/labor:append",
    tags=["ERP Ledgers"],
)
def append_labor(
    tenant_id: int,
    location_id: int,
    payload: LaborLedgerAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.labor]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["labor"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class StockoutLedgerEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    item_id: Optional[int] = None
    item_name_raw: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    reason: Optional[str] = None


class StockoutLedgerAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[StockoutLedgerEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/stockout-86:append",
    tags=["ERP Ledgers"],
)
def append_stockout_ledger(
    tenant_id: int,
    location_id: int,
    payload: StockoutLedgerAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.events]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["stockout-86"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class CostingInputEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    payload: dict


class CostingInputsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[CostingInputEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/costing-inputs:append",
    tags=["ERP Ledgers"],
)
def append_costing_inputs(
    tenant_id: int,
    location_id: int,
    payload: CostingInputsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.events]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["costing-inputs"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class OpsIncidentEvent(BaseModel):
    source_event_id: str
    event_time: Optional[datetime] = None
    payload: dict


class OpsIncidentsAppendRequest(BaseModel):
    source_system_id: int
    business_day_id: Optional[int] = None
    events: list[OpsIncidentEvent]


@app.post(
    "/v1/tenants/{tenant_id}/locations/{location_id}/erp-ledger/ops-incidents:append",
    tags=["ERP Ledgers"],
)
def append_ops_incidents(
    tenant_id: int,
    location_id: int,
    payload: OpsIncidentsAppendRequest,
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.events]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        ERP_LEDGER_FEED_TYPES["ops-incidents"],
        "ERP",
        payload.source_system_id,
        payload.business_day_id,
        events,
    )


class MasterDataEvent(BaseModel):
    source_event_id: str
    event_time: datetime
    entity_type: str
    payload: dict


class MasterDataLedgerAppendRequest(BaseModel):
    source_system_id: int
    events: list[MasterDataEvent]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system_id": 2,
                "events": [
                    {
                        "source_event_id": "item_11_v3",
                        "event_time": "2026-01-01T00:00:00+05:00",
                        "entity_type": "ITEM",
                        "payload": {
                            "item_id": 11,
                            "item_name": "Chicken Breast",
                            "base_uom": "g",
                            "item_type": "INGREDIENT",
                        },
                    }
                ],
            }
        }
    }


@app.post("/v1/tenants/{tenant_id}/master-data-ledger:append", tags=["ERP Ledgers"])
def append_master_data_ledger(
    tenant_id: int,
    payload: MasterDataLedgerAppendRequest,
    location_id: int = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    events = [event.model_dump() for event in payload.events]
    return _append_ledger_events(
        db,
        tenant_id,
        location_id,
        "MASTER_DATA_LEDGER",
        "MASTER",
        payload.source_system_id,
        None,
        events,
    )
