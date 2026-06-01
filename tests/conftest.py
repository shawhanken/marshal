import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db_session():
    # 单元测试用内存 SQLite;集成测试单独用 Postgres。
    engine = create_engine("sqlite:///:memory:")
    from marshal_core.knowledge.models import Base
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
