from session_store import ConcurrentRunError, SessionStore


def test_create_session_starts_with_empty_history_and_slots():
    store = SessionStore()

    session = store.create_session()

    assert session.session_id
    assert session.messages == []
    assert session.slots.platform is None
    assert session.slots.brand is None
    assert session.slots.count is None
    assert session.active_run_id is None


def test_update_slots_and_append_message_preserve_existing_session_state():
    store = SessionStore()
    session = store.create_session()

    store.append_message(session.session_id, "user", "帮我看一下 Blackview 的竞品")
    store.update_slots(session.session_id, brand="Blackview")
    store.update_slots(session.session_id, count=5)

    saved = store.get_session(session.session_id)
    assert [(message.role, message.content) for message in saved.messages] == [
        ("user", "帮我看一下 Blackview 的竞品")
    ]
    assert saved.slots.platform is None
    assert saved.slots.brand == "Blackview"
    assert saved.slots.count == 5


def test_start_run_rejects_second_active_run_for_same_session():
    store = SessionStore()
    session = store.create_session()

    run_id = store.start_run(session.session_id)

    assert run_id

    try:
        store.start_run(session.session_id)
    except ConcurrentRunError as exc:
        assert "already active" in str(exc)
    else:
        raise AssertionError("ConcurrentRunError was not raised")
