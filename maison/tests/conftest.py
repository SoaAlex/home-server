import os
import pytest
from app import db


@pytest.fixture
def dburl():
    url = os.environ["TEST_DATABASE_URL"]
    with db.connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS listing_photo, duplicate_pair, listing CASCADE")
    db.init_schema(url)
    return url
