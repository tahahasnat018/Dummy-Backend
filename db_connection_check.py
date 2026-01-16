from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings


def main() -> None:
    database_url = settings.database_url
    print(f"DATABASE_URL={database_url}")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("DB connection OK")
    except SQLAlchemyError as exc:
        print("DB connection FAILED")
        print(exc)


if __name__ == "__main__":
    main()
