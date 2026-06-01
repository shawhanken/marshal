import pytest
from marshal_core.knowledge.store import Store


def test_open_escape_creates_open_entry(db_session):
    store = Store(db_session)
    esc = store.open_escape(id="esc-0001", description="bare 2**10000 绕过 int guard",
                            root_cause_class="determinism-gap")
    assert esc.id == "esc-0001"
    assert esc.status == "open"
    assert esc.spawned_check is None
    assert store.list_open_escapes()[0].id == "esc-0001"


def test_close_escape_sets_spawned_check_and_status(db_session):
    store = Store(db_session)
    store.open_escape(id="esc-0002", description="d", root_cause_class="c")
    store.close_escape("esc-0002", spawned_check="det.bare_pow_literal")
    esc = store.get_escape("esc-0002")
    assert esc.status == "closed"
    assert esc.spawned_check == "det.bare_pow_literal"
    assert store.list_open_escapes() == []


def test_close_escape_without_spawned_check_raises(db_session):
    store = Store(db_session)
    store.open_escape(id="esc-0003", description="d", root_cause_class="c")
    with pytest.raises(ValueError, match="spawned_check"):
        store.close_escape("esc-0003", spawned_check="")
