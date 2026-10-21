"""
Test suite for NEXUS autonomous live background sync, rebranding, and real-time feeds.
"""
import sys
import os
import sqlite3
import pytest

# Add files/ to python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "files"))

from web_app import app, BRAND_NAME, BRAND_SUB, _build_triage_prompt, _init_db, _ensure_background_thread

def test_branding_is_nexus():
    """Verify that the platform branding is rebranded to NEXUS."""
    assert BRAND_NAME == "NEXUS"
    assert "Autonomous Executive Email Intelligence" in BRAND_SUB

def test_triage_prompt_uses_nexus():
    """Verify that the AI prompt instructs the LLM as NEXUS."""
    prompt = _build_triage_prompt("Reshma", "Best regards, Reshma")
    assert "NEXUS" in prompt
    assert "Reshma" in prompt

def test_live_feed_unauthenticated():
    """Verify that /api/live_feed returns 401 when unauthenticated."""
    client = app.test_client()
    res = client.get("/api/live_feed")
    assert res.status_code == 401
    data = res.get_json()
    assert "error" in data

def test_live_feed_authenticated_returns_metrics_and_emails():
    """Verify that an authenticated user receives real-time live feed data."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user'] = {
            'email': 'reshma@example.com',
            'name': 'Reshma',
            'role': 'owner',
            'reply_mode': 'draft'
        }
        sess['credentials'] = {
            'token': 'mock-token',
            'refresh_token': 'mock-refresh',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'client_id': 'mock-id',
            'client_secret': 'mock-secret',
            'scopes': ['https://www.googleapis.com/auth/gmail.readonly']
        }
        sess['connected_at'] = 1729000000

    res = client.get("/api/live_feed")
    assert res.status_code == 200
    data = res.get_json()
    assert "stats" in data
    assert "emails" in data
    assert "total" in data["stats"]
    assert "replies" in data["stats"]
    assert "high_priority" in data["stats"]
    assert "spam" in data["stats"]
    assert "agent_status" in data["stats"]

def test_background_daemon_thread_started():
    """Verify background sync daemon initializes cleanly."""
    _ensure_background_thread()
    from web_app import _agent_state
    assert "poll_interval" in _agent_state
    assert _agent_state["poll_interval"] == 20 or _agent_state["poll_interval"] == 15

def test_oauth_login_stores_code_verifier():
    """Verify that /login generates and stores PKCE code_verifier in session."""
    client = app.test_client()
    res = client.get("/login")
    if res.status_code == 302:
        with client.session_transaction() as sess:
            assert 'state' in sess
            assert 'code_verifier' in sess
            assert sess['code_verifier'] is not None

def test_oauth_callback_invalid_grant_redirects_to_login():
    """Verify that an invalid/stale authorization callback safely redirects to /login rather than 500 error."""
    client = app.test_client()
    res = client.get("/oauth2callback?code=fake_code_123&state=fake_state")
    assert res.status_code == 302
    assert "/login" in res.headers.get("Location", "")
