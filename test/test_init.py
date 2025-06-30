import importlib
import os
from sqlalchemy import inspect

def test_db_initialization(monkeypatch):
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', 'dummy')
    monkeypatch.setenv('LINE_CHANNEL_SECRET', 'dummy')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')

    if 'app' in globals():
        importlib.reload(globals()['app'])
    import app
    importlib.reload(app)  # ensure reload with new env vars

    inspector = inspect(app.engine)
    assert 'users' in inspector.get_table_names()
    # simple query should not raise
    session = app.Session()
    session.query(app.User).all()
    session.close()