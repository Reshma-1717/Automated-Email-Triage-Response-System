"""
Comprehensive test suite for Multi-User Architecture and Owner Portal.
"""

import os
import sys
import sqlite3
import pytest

# Ensure files directory is on path
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "files"))
sys.path.insert(0, APP_DIR)

from web_app import app, _init_db, _build_triage_prompt, OWNER_EMAIL, _db

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key-multiuser'
    with app.test_client() as client:
        yield client


def test_dynamic_triage_prompt():
    """Verify that triage prompt is dynamically personalized and contains no hardcoded names."""
    prompt_alex = _build_triage_prompt(user_name="Alex Mercer", signature="Alex")
    assert "Alex Mercer" in prompt_alex
    assert 'signed "Alex"' in prompt_alex
    assert "Reshma" not in prompt_alex

    prompt_sarah = _build_triage_prompt(user_name="Sarah Connor", signature="Best, Sarah")
    assert "Sarah Connor" in prompt_sarah
    assert 'signed "Best, Sarah"' in prompt_sarah
    assert "Reshma" not in prompt_sarah


def test_database_schema_multi_user():
    """Verify database schema has users table and user_email column."""
    _init_db()
    with _db() as conn:
        # Check users table
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "users" in tables
        assert "processed_emails" in tables

        # Check columns of processed_emails
        cols = [r[1] for r in conn.execute("PRAGMA table_info(processed_emails)").fetchall()]
        assert "user_email" in cols
        assert "status" in cols


def test_multi_tenant_data_isolation(client):
    """Verify User A cannot see User B's statistics or replies."""
    _init_db()
    user_a = "alice_test@example.com"
    user_b = "bob_test@example.com"

    # Insert test data for Alice and Bob
    with _db() as conn:
        conn.execute("DELETE FROM processed_emails WHERE user_email IN (?, ?)", (user_a, user_b))
        
        # Alice has 2 emails, 1 reply
        conn.execute("""
            INSERT INTO processed_emails (id, user_email, subject, sender, received_at, classification, priority, reply_sent, reply_text, status)
            VALUES ('alice_msg_1', ?, 'Alice Job Offer', 'hr@tech.com', '2026-10-21T10:00:00', 'important', 'high', 1, 'Thanks, Alice', 'completed')
        """, (user_a,))
        conn.execute("""
            INSERT INTO processed_emails (id, user_email, subject, sender, received_at, classification, priority, reply_sent, reply_text, status)
            VALUES ('alice_msg_2', ?, 'Alice Newsletter', 'news@daily.com', '2026-10-21T10:05:00', 'normal', 'low', 0, '', 'completed')
        """, (user_a,))

        # Bob has 1 email, 0 replies, 1 spam
        conn.execute("""
            INSERT INTO processed_emails (id, user_email, subject, sender, received_at, classification, priority, reply_sent, reply_text, status)
            VALUES ('bob_msg_1', ?, 'Bob Lottery Spam', 'scam@win.com', '2026-10-21T10:10:00', 'spam', 'low', 0, '', 'completed')
        """, (user_b,))
        conn.commit()

    # Test as Alice
    with client.session_transaction() as sess:
        sess['credentials'] = {'token': 'fake_token'}
        sess['user'] = {'email': user_a, 'name': 'Alice', 'role': 'user'}

    res_a_stats = client.get('/stats').get_json()
    assert res_a_stats['total_processed'] == 2
    assert res_a_stats['replies_sent'] == 1
    assert res_a_stats['high_priority'] == 1
    assert res_a_stats['spam_blocked'] == 0

    res_a_replies = client.get('/replies').get_json()
    assert len(res_a_replies['replies']) == 1
    assert res_a_replies['replies'][0]['subject'] == 'Alice Job Offer'

    # Test as Bob
    with client.session_transaction() as sess:
        sess['credentials'] = {'token': 'fake_token'}
        sess['user'] = {'email': user_b, 'name': 'Bob', 'role': 'user'}

    res_b_stats = client.get('/stats').get_json()
    assert res_b_stats['total_processed'] == 1
    assert res_b_stats['replies_sent'] == 0
    assert res_b_stats['high_priority'] == 0
    assert res_b_stats['spam_blocked'] == 1

    res_b_replies = client.get('/replies').get_json()
    assert len(res_b_replies['replies']) == 0  # Bob has 0 replies


def test_admin_portal_access_control(client):
    """Verify unauthenticated -> 302, non-owner -> 403, owner -> 200."""
    # 1. Unauthenticated
    res_unauth = client.get('/admin')
    assert res_unauth.status_code == 302

    # 2. Authenticated as non-owner
    with client.session_transaction() as sess:
        sess['credentials'] = {'token': 'fake_token'}
        sess['user'] = {'email': 'regular_user@gmail.com', 'name': 'Regular', 'role': 'user'}

    res_user = client.get('/admin')
    assert res_user.status_code == 403

    # 3. Authenticated as Owner
    with client.session_transaction() as sess:
        sess['credentials'] = {'token': 'fake_token'}
        sess['user'] = {'email': OWNER_EMAIL, 'name': 'Owner Admin', 'role': 'owner'}

    res_owner = client.get('/admin')
    assert res_owner.status_code == 200
    assert b"Owner Command Center" in res_owner.data
