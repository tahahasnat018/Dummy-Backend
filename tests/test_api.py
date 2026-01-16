from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app, get_db


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_create_tenant_location_asset_flow() -> None:
    client = _make_client()
    with client:
        tenant_resp = client.post("/api/v1/tenants", json={"name": "Savour Foods", "is_active": True})
        assert tenant_resp.status_code == 200
        tenant_id = tenant_resp.json()["data"]["tenant_id"]

        location_resp = client.post(
            "/api/v1/locations",
            json={
                "tenant_id": tenant_id,
                "name": "Blue Area",
                "timezone": "Asia/Karachi",
                "currency_code": "PKR",
                "is_active": True,
            },
        )
        assert location_resp.status_code == 200
        location_id = location_resp.json()["data"]["location_id"]

        asset_resp = client.post(
            "/api/v1/assets",
            json={
                "tenant_id": tenant_id,
                "location_id": location_id,
                "asset_name": "Rice Cooker #1",
                "asset_type": "kitchen",
                "serial_number": "RC-001",
                "is_critical": True,
                "is_active": True,
            },
        )
        assert asset_resp.status_code == 200
        asset_data = asset_resp.json()["data"]
        assert asset_data["asset_id"] > 0
        assert asset_data["location_id"] == location_id


def test_create_and_list_source_systems() -> None:
    client = _make_client()
    with client:
        create_resp = client.post(
            "/api/v1/source-systems",
            json={"name": "pos_square", "type": "POS", "is_active": True, "tenant_id": 1},
        )
        assert create_resp.status_code == 200

        list_resp = client.get("/api/v1/source-systems", params={"tenant_id": 1, "is_active": True})
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "pos_square"
