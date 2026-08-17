"""Public feedback form and its admin-only listing on /admin."""
import base64

import feedback


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_feedback_form_loads(client):
    resp = client.get("/feedback")
    assert resp.status_code == 200
    assert b"Feedback" in resp.data


def test_submitting_feedback_saves_it(client):
    before = feedback.count_feedback()
    resp = client.post("/feedback", data={
        "name": "Test User", "email": "test@example.com",
        "message": "This is a test suggestion.", "page": "/crypto",
    }, follow_redirects=True)
    assert resp.status_code == 200
    after = feedback.count_feedback()
    assert after == before + 1
    latest = feedback.list_feedback(limit=1)[0]
    assert latest["message"] == "This is a test suggestion."
    assert latest["name"] == "Test User"


def test_submitting_empty_message_is_rejected(client):
    before = feedback.count_feedback()
    client.post("/feedback", data={"name": "X", "message": ""}, follow_redirects=True)
    after = feedback.count_feedback()
    assert after == before


def test_honeypot_field_silently_drops_submission(client):
    before = feedback.count_feedback()
    client.post("/feedback", data={
        "message": "spam message", "website": "http://spam.example",
    }, follow_redirects=True)
    after = feedback.count_feedback()
    assert after == before


def test_admin_dashboard_shows_feedback(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "secret123")

    client.post("/feedback", data={
        "name": "Visible User", "email": "visible-user@example.com",
        "message": "Shows up in admin?",
    })

    resp = client.get(
        "/admin?section=feedback",
        headers=_basic_auth_header("admin", "secret123"),
    )
    assert resp.status_code == 200
    assert b"Visible User" in resp.data
    assert b"Shows up in admin?" in resp.data


def test_feedback_link_in_public_nav(client):
    resp = client.get("/")
    assert b'href="/feedback"' in resp.data
