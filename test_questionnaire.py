"""Integration tests for the questionnaire kiosk backend."""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:3050"


def api(method, path, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def fetch(path):
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return resp.status, resp.read().decode()


def wait_for_server(timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            api("GET", "/api/state")
            return True
        except Exception:
            time.sleep(0.3)
    return False


# --- Tests ---

def test_health():
    status, data = api("GET", "/api/state")
    assert status == 200
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    print("  health check OK")


def test_create_and_serve():
    status, data = api("POST", "/api/create", {
        "type": "multiple-choice",
        "payload": {
            "question": "Test question?",
            "options": [{"label": "A"}, {"label": "B"}],
        },
        "id": "test-mc",
    })
    assert status == 201
    assert data["id"] == "test-mc"
    assert data["type"] == "multiple-choice"
    assert not data["is_persistent"]

    # Serve template
    status, html = fetch("/test-mc")
    assert status == 200
    assert "Test question?" in html
    assert 'const port = \'test-mc\'' in html
    assert '/api/respond/test-mc' in html
    assert '/static/style.css' in html

    # Duplicate ID
    status, data = api("POST", "/api/create", {
        "type": "confirm",
        "payload": {"question": "Dup?"},
        "id": "test-mc",
    })
    assert status == 409
    print("  create + serve OK")


def test_respond_oneshot():
    status, data = api("POST", "/api/respond/test-mc", {
        "answer": "A",
        "answer_index": 0,
        "type": "multiple-choice",
    })
    assert status == 201
    assert data["response_id"] >= 1

    # Second response should fail (one-shot, no allow_multiple)
    status, data = api("POST", "/api/respond/test-mc", {
        "answer": "B",
        "answer_index": 1,
    })
    assert status in (409, 410)

    # Check auto-closed
    status, data = api("GET", "/api/response/test-mc")
    assert status == 200
    assert data["closed"] is True
    assert data["response_count"] == 1
    assert data["responses"][0]["response_data"]["answer"] == "A"
    print("  one-shot respond OK")


def test_persistent_toggle():
    status, data = api("POST", "/api/create", {
        "type": "toggle",
        "payload": {"question": "Toggle test?", "initial_state": False},
        "id": "test-toggle",
    })
    assert status == 201
    assert data["is_persistent"] is True

    # Multiple responses accepted
    for val in [True, False, True]:
        status, data = api("POST", "/api/respond/test-toggle", {
            "value": val, "type": "toggle",
        })
        assert status == 201

    # All stored
    status, data = api("GET", "/api/response/test-toggle")
    assert status == 200
    assert data["response_count"] == 3
    assert data["closed"] is False

    # Latest only
    status, data = api("GET", "/api/response/test-toggle?latest=true")
    assert data["responses"][0]["response_data"]["value"] is True
    print("  persistent toggle OK")


def test_close_questionnaire():
    status, data = api("DELETE", "/api/questionnaire/test-toggle")
    assert status == 200
    assert data["response_count"] == 3

    # Respond to closed should fail
    status, data = api("POST", "/api/respond/test-toggle", {"value": True})
    assert status == 410

    # Closed template shows banner
    status, html = fetch("/test-toggle")
    assert "This questionnaire has been closed" in html
    print("  close questionnaire OK")


def test_list_questionnaires():
    status, data = api("GET", "/api/questionnaires?active=false")
    assert status == 200
    ids = {q["id"] for q in data["questionnaires"]}
    assert "test-mc" in ids
    assert "test-toggle" in ids

    # Active only should show neither (both closed)
    status, data = api("GET", "/api/questionnaires")
    ids = {q["id"] for q in data["questionnaires"]}
    assert "test-mc" not in ids
    assert "test-toggle" not in ids
    print("  list questionnaires OK")


def test_index_page():
    # Create an active one for the index
    api("POST", "/api/create", {
        "type": "confirm",
        "payload": {"question": "Index test?"},
        "id": "test-index",
    })
    status, html = fetch("/")
    assert status == 200
    assert "Index test?" in html
    assert "test-index" in html
    print("  index page OK")


def test_404():
    try:
        with urllib.request.urlopen(f"{BASE}/nonexistent") as resp:
            assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404
    print("  404 OK")


def test_ask_channel_flow():
    """Test the ask/channel flow: send questions to same ID, get responses, repeat."""
    # Create initial question at channel ID
    status, data = api("POST", "/api/create", {
        "type": "multiple-choice",
        "payload": {
            "question": "First question?",
            "options": [{"label": "A"}, {"label": "B"}],
        },
        "id": "test-channel",
    })
    assert status == 201

    # Verify it's served
    status, html = fetch("/test-channel")
    assert "First question?" in html
    # Verify SSE auto-reload script is injected
    assert "new EventSource" in html
    assert "new_question" in html

    # Respond to first question
    status, data = api("POST", "/api/respond/test-channel", {
        "answer": "A", "answer_index": 0, "type": "multiple-choice",
    })
    assert status == 201

    # Now replace with a new question via /api/ask
    status, data = api("POST", "/api/ask/test-channel", {
        "type": "confirm",
        "payload": {"question": "Second question?"},
    })
    assert status == 200
    assert data["type"] == "confirm"
    assert data["id"] == "test-channel"

    # Old responses are gone (replaced), new question is served
    status, html = fetch("/test-channel")
    assert "Second question?" in html
    assert "First question?" not in html

    # Respond to second question
    status, data = api("POST", "/api/respond/test-channel", {
        "answer": "Yes", "confirmed": True, "type": "confirm",
    })
    assert status == 201

    # Replace again with a toggle (persistent type)
    status, data = api("POST", "/api/ask/test-channel", {
        "type": "toggle",
        "payload": {"question": "Third question - toggle?", "initial_state": False},
    })
    assert status == 200
    assert data["is_persistent"] is True

    # Toggle responds multiple times
    status, data = api("POST", "/api/respond/test-channel", {"value": True, "type": "toggle"})
    assert status == 201
    status, data = api("POST", "/api/respond/test-channel", {"value": False, "type": "toggle"})
    assert status == 201

    # Verify current state
    status, data = api("GET", "/api/response/test-channel")
    assert data["response_count"] == 2
    assert data["type"] == "toggle"

    print("  ask/channel flow OK")


def test_ask_creates_new_channel():
    """Test that /api/ask works even when no questionnaire exists at that ID yet."""
    status, data = api("POST", "/api/ask/test-new-channel", {
        "type": "confirm",
        "payload": {"question": "Brand new channel?"},
    })
    assert status == 200
    assert data["id"] == "test-new-channel"

    status, html = fetch("/test-new-channel")
    assert "Brand new channel?" in html
    print("  ask creates new channel OK")


def test_validation():
    # Bad type
    status, data = api("POST", "/api/create", {
        "type": "invalid-type",
        "payload": {"question": "X"},
    })
    assert status == 422

    # Missing question
    status, data = api("POST", "/api/create", {
        "type": "confirm",
        "payload": {"no_question": True},
    })
    assert status == 422
    print("  validation OK")


# --- Cleanup ---

def cleanup():
    """Remove test questionnaires."""
    for qid in ["test-mc", "test-toggle", "test-index", "test-channel", "test-new-channel"]:
        try:
            api("DELETE", f"/api/questionnaire/{qid}")
        except Exception:
            pass


# --- Runner ---

if __name__ == "__main__":
    print("Waiting for server...")
    if not wait_for_server():
        print("Server not available at", BASE)
        sys.exit(1)

    tests = [
        test_health,
        test_create_and_serve,
        test_respond_oneshot,
        test_persistent_toggle,
        test_close_questionnaire,
        test_list_questionnaires,
        test_index_page,
        test_ask_channel_flow,
        test_ask_creates_new_channel,
        test_404,
        test_validation,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1

    cleanup()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
