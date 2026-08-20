from marshal_core import config


def test_db_url_env_override_always_wins(monkeypatch):
    monkeypatch.setenv("MARSHAL_DB", "sqlite:///explicit.db")
    assert config.db_url() == "sqlite:///explicit.db"


def test_db_url_defaults_to_marshal_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MARSHAL_DB", raising=False)
    monkeypatch.setenv("MARSHAL_HOME", str(tmp_path))
    assert config.db_url() == f"sqlite:///{tmp_path / 'marshal.db'}"
