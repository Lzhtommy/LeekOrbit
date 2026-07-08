import pytest

from leekorbit import db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.configure(tmp_path / "test.db")
    yield
