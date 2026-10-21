"""
NEXUS AI — Autonomous Executive Email Intelligence
World-Class Multi-Tenant Platform with Real-Time Background Triage & Executive UI.
"""

from flask import Flask, redirect, request, session, url_for, render_template_string, jsonify
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os, json, base64, re, sqlite3, threading, time as _time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import openai as _openai

# ── Platform Branding & Settings ──────────────────────────────────────────
BRAND_NAME    = "NEXUS"
BRAND_SUB     = "Autonomous Executive Email Intelligence"
OWNER_EMAIL   = os.getenv("OWNER_EMAIL", "reshmakodavali@gmail.com").strip().lower()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "nexus-quantum-secret-key-prod-992")
app.permanent_session_lifetime = timedelta(days=7)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = os.getenv("OAUTHLIB_INSECURE_TRANSPORT", "1")

# ── Persistent SQLite DB (Multi-Tenant) ────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "aura_stats.db")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email        TEXT PRIMARY KEY,
                name         TEXT,
                picture      TEXT,
                role         TEXT DEFAULT 'user',
                reply_mode   TEXT DEFAULT 'draft',
                signature    TEXT DEFAULT '',
                connected_at TEXT,
                last_active  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                id             TEXT PRIMARY KEY,
                user_email     TEXT DEFAULT '',
                subject        TEXT,
                sender         TEXT,
                received_at    TEXT,
                classification TEXT,
                priority       TEXT,
                reply_sent     INTEGER DEFAULT 0,
                reply_text     TEXT DEFAULT '',
                status         TEXT DEFAULT 'completed'
            )
        """)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(processed_emails)").fetchall()]
        if "user_email" not in cols:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN user_email TEXT DEFAULT ''")
        if "status" not in cols:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN status TEXT DEFAULT 'completed'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_user ON processed_emails (user_email, received_at)")
        conn.commit()

_init_db()

# ── LLM Configuration ──────────────────────────────────────────────────────
_LLM_API_KEY  = os.getenv("OPENAI_API_KEY", "")
_LLM_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
_LLM_MODEL    = os.getenv("MODEL_NAME", "gpt-4o-mini")

def _build_triage_prompt(user_name: str = "User", signature: str = None) -> str:
    sig = signature if signature else user_name
    return f"""You are NEXUS, an elite autonomous executive AI email copilot for {user_name}.
Your job is to read incoming emails, analyze urgency, classify them accurately, and prepare professional replies.

You must return valid JSON ONLY with exactly this structure:
{{
  "classification": "spam" | "important" | "normal",
  "priority": "high" | "medium" | "low",
  "needs_reply": true | false,
  "reply": "<professional response string or empty string>",
  "reason": "<1 concise sentence explaining the triage decision>"
}}

Classification Guidelines:
- "spam": Phishing, scams, unsolicited promotions, bulk marketing, deceptive alerts.
- "important": Direct personal emails addressed to {user_name}, interviews, project updates, human action items, urgent deadlines.
- "normal": Newsletters, automated platform notifications, job alerts, receipts, standard digests.

Priority Matrix:
- "high": Immediate action needed, deadlines within 48h, interview requests, executive inquiries.
- "medium": Genuine questions requiring a reply within a few days.
- "low": Informational, newsletters, low-relevance notifications.

Reply Rules (Crucial):
- needs_reply: true ONLY when an actual person wrote personally to {user_name} and expects a personal response.
- needs_reply: false for ALL spam, newsletters, platform alerts, no-reply senders, receipts, and automated notifications.
- If is_automated=true or is_bulk=true, ALWAYS set needs_reply: false and reply: "".
- If needs_reply is true, draft a concise, polished, warm reply (under 75 words) speaking as {user_name}, signed "{sig}".

Return valid JSON only. No markdown formatting, no commentary."""

def _llm_triage(subject: str, sender: str, body: str, is_automated: bool, is_bulk: bool, user_name: str = "User", signature: str = None) -> dict:
    fallback = {
        "classification": "normal",
        "priority": "low" if (is_automated or is_bulk) else "medium",
        "needs_reply": False,
        "reply": "",
        "reason": "Standard automated email classification."
    }
    if not _LLM_API_KEY:
        return fallback
    try:
        client = _openai.OpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL)
        prompt = _build_triage_prompt(user_name=user_name, signature=signature)
        user_msg = (
            f"Context: is_automated={is_automated}, is_bulk={is_bulk}\n\n"
            f"Subject: {subject}\n"
            f"From: {sender}\n\n"
            f"Body:\n{body[:2500]}"
        )
        resp = client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": user_msg}
            ],
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        result = json.loads(raw)
        if is_automated or is_bulk:
            result["needs_reply"] = False
            result["reply"] = ""
        return result
    except Exception as e:
        print(f"LLM triage notice: {e}")
        return fallback

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRETS_FILE = os.path.join(_APP_DIR, "credentials.json") if os.path.exists(os.path.join(_APP_DIR, "credentials.json")) else "credentials.json"

# ── Background Autonomous Agent State ──────────────────────────────────────
_agent_state = {
    "running":       False,
    "credentials":   None,
    "user_info":     None,
    "poll_interval": 20,      # Autonomous check every 20 seconds
    "last_poll":     None,
    "status":        "idle",  # idle | polling | error
}
_agent_lock = threading.Lock()

def _process_inbox(creds_dict: dict, connected_at: int, user_info: dict = None, reply_mode: str = "draft"):
    """Core autonomous processing logic — fetches unseen emails, triages with AI, and prepares response."""
    from google.oauth2.credentials import Credentials as _Creds
    from googleapiclient.discovery import build as _build

    creds = _Creds(**creds_dict)
    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        except Exception as e:
            print(f"Token refresh error: {e}")

    service = _build('gmail', 'v1', credentials=creds)

    user_info  = user_info or {}
    user_email = user_info.get("email", "").lower()
    user_name  = user_info.get("name") or (user_email.split("@")[0].capitalize() if user_email else "User")
    signature  = user_info.get("signature") or user_name

    if not user_email:
        try:
            prof = service.users().getProfile(userId='me').execute()
            user_email = prof.get('emailAddress', '').lower()
            if user_name == "User" and user_email:
                user_name = user_email.split("@")[0].capitalize()
                signature = user_name
        except Exception:
            pass

    OWN_EMAIL_ADDRESSES = [user_email] if user_email else []

    NO_REPLY_PATTERNS = [
        "no-reply@", "noreply@", "mailer-daemon@", "postmaster@",
        "do-not-reply@", "donotreply@", "notifications@", "notice@",
        "alert@", "alerts@", "automated@", "support@", "info@",
        "team@", "hello@", "hi@", "contact@", "help@", "update@",
        "security@", "newsletter@", "news@", "marketing",
        "@naukri", "@internshala", "@linkedin", "@ambitionbox", "@unstop",
        "@thesmartbridge", "@zoom.us", "@udemy", "@coursera",
        "@infosys", "@wipro", "@tcs", "@tcsion", "@google", "@microsoft",
        "@amazon", "@anthropic", "@github", "@flipkart",
        "@aspireforher", "@substack", "@servicenow", "@xbox",
        "@nptel", "@iitm.ac.in", "@simplilearnmailer",
        "@e.xbox", "@mails.", "@mailer.", "@campaign.", "@email.",
    ]
    BULK_SENDER_PATTERNS = NO_REPLY_PATTERNS

    BOUNCE_SUBJECTS = [
        "delivery status", "undeliverable", "mail delivery",
        "delivery failure", "returned mail", "delivery notification"
    ]

    with _db() as conn:
        seen = {r[0] for r in conn.execute("SELECT id FROM processed_emails WHERE user_email = ? OR user_email = ''", (user_email,)).fetchall()}

    # Fetch latest 20 messages in INBOX (without time boundary to capture all new arrivals reliably)
    results = service.users().messages().list(
        userId='me',
        q='label:INBOX -from:mailer-daemon -from:postmaster',
        maxResults=20
    ).execute()
    messages = results.get('messages', [])

    candidates = []
    for msg in messages:
        if msg['id'] in seen:
            continue
        try:
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='full'
            ).execute()
        except Exception:
            continue

        headers   = msg_data['payload'].get('headers', [])
        subject   = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '(No Subject)')
        sender    = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'unknown')
        mid       = next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
        thread_id = msg_data.get('threadId', msg['id'])
        sl = subject.lower(); sdl = sender.lower()

        if any(p in sl for p in BOUNCE_SUBJECTS):
            continue

        def _extract_email_address(s):
            m = re.search(r'<([^>]+)>', s)
            return m.group(1).lower() if m else s.lower()

        def _extract_body(part):
            if part.get('mimeType') == 'text/plain':
                data = part.get('body', {}).get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            for p in part.get('parts', []):
                r = _extract_body(p)
                if r: return r
            return ""

        body     = _extract_body(msg_data.get('payload', {}))[:2500]
        is_own   = any(e in _extract_email_address(sender) for e in OWN_EMAIL_ADDRESSES)
        is_auto  = (not is_own) and any(p in sdl for p in NO_REPLY_PATTERNS)
        is_bulk  = (not is_own) and any(p in sdl for p in BULK_SENDER_PATTERNS)
        sort_key = 1 if (is_auto or is_bulk) else 0
        candidates.append((sort_key, msg['id'], subject, sender, mid, thread_id, is_auto, is_bulk, body))

    candidates.sort(key=lambda x: x[0])

    for (_, msg_id, subject, sender, mid, thread_id, is_auto, is_bulk, body) in candidates:
        try:
            with _db() as conn:
                conn.execute("""
                    INSERT INTO processed_emails
                      (id, user_email, subject, sender, received_at, classification, priority, reply_sent, reply_text, status)
                    VALUES (?, ?, ?, ?, ?, 'pending', 'pending', 0, '', 'processing')
                """, (msg_id, user_email, subject[:120], sender[:120], datetime.now().isoformat(timespec='seconds')))
                conn.commit()
        except sqlite3.IntegrityError:
            continue

        triage      = _llm_triage(subject, sender, body, is_auto, is_bulk, user_name=user_name, signature=signature)
        classif     = triage.get("classification", "normal")
        priority    = triage.get("priority", "low")
        needs_reply = triage.get("needs_reply", False)
        reply_text  = triage.get("reply", "")
        reply_sent  = False
        status_action = "classified"

        if needs_reply and reply_text:
            try:
                reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
                message = MIMEText(reply_text)
                message['to']      = sender
                message['subject'] = reply_subject
                if mid:
                    message['In-Reply-To'] = mid
                    message['References']  = mid
                raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

                if reply_mode == "send":
                    service.users().messages().send(
                        userId='me', body={'raw': raw, 'threadId': thread_id}
                    ).execute()
                    reply_sent = True
                    status_action = "sent"
                else: # Default safe mode: creates a Gmail draft for review
                    service.users().drafts().create(
                        userId='me', body={'message': {'raw': raw, 'threadId': thread_id}}
                    ).execute()
                    reply_sent = True
                    status_action = "drafted"
            except Exception as e:
                reply_sent = False
                status_action = "error"

        with _db() as conn:
            conn.execute("""
                UPDATE processed_emails
                SET classification = ?, priority = ?, reply_sent = ?, reply_text = ?, status = ?
                WHERE id = ? AND (user_email = ? OR user_email = '')
            """, (
                classif, priority, int(reply_sent),
                reply_text if reply_sent else "",
                status_action,
                msg_id,
                user_email
            ))
            conn.commit()


def _background_loop():
    """Runs forever in a daemon thread — autonomously polls inboxes every 20 seconds."""
    while True:
        with _agent_lock:
            creds        = _agent_state.get("credentials")
            connected_at = _agent_state.get("connected_at", 0)
            user_info    = _agent_state.get("user_info")
            interval     = _agent_state.get("poll_interval", 20)

        if creds:
            try:
                with _agent_lock:
                    _agent_state["status"]    = "polling"
                    _agent_state["last_poll"] = datetime.now().isoformat(timespec='seconds')
                
                reply_mode = (user_info or {}).get("reply_mode", "draft")
                _process_inbox(creds, connected_at, user_info=user_info, reply_mode=reply_mode)
                
                with _agent_lock:
                    _agent_state["status"] = "idle"
            except Exception as e:
                print(f"Background poll notice: {e}")
                with _agent_lock:
                    _agent_state["status"] = "idle"

        _time.sleep(interval)

_bg_started = False
def _ensure_background_thread():
    global _bg_started
    if not _bg_started:
        _bg_started = True
        t = threading.Thread(target=_background_loop, daemon=True)
        t.start()

_ensure_background_thread()


# ══════════════════════════════════════════════════════════════════════════════
# 1. LANDING PAGE (Modern Executive Luxury Dark Theme)
# ══════════════════════════════════════════════════════════════════════════════
HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS — Autonomous Executive Email Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-void: #05070E;
            --surface-1: #0C101D;
            --surface-2: #141A2E;
            --surface-3: #1C243F;
            --accent-primary: #6366F1;
            --accent-glow: rgba(99, 102, 241, 0.45);
            --cyan: #06B6D4;
            --gold: #F59E0B;
            --emerald: #10B981;
            --border: rgba(99, 102, 241, 0.16);
            --border-hover: rgba(6, 182, 212, 0.35);
            --text-main: #F8FAFC;
            --text-sub: #94A3B8;
            --text-dim: #64748B;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-void);
            color: var(--text-main);
            min-height: 100vh;
            overflow-x: hidden;
            line-height: 1.5;
        }
        /* Glowing Ambient Background */
        .ambient-glow {
            position: fixed;
            width: 700px;
            height: 700px;
            border-radius: 50%;
            filter: blur(150px);
            pointer-events: none;
            z-index: 0;
            opacity: 0.18;
        }
        .glow-1 { top: -200px; left: 50%; transform: translateX(-50%); background: radial-gradient(circle, var(--accent-primary) 0%, var(--cyan) 100%); }
        .glow-2 { bottom: -100px; right: -50px; background: radial-gradient(circle, #8B5CF6 0%, transparent 70%); }

        nav {
            position: fixed;
            top: 0; left: 0; right: 0;
            z-index: 100;
            padding: 20px 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(5, 7, 14, 0.7);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
        }
        .brand-icon {
            width: 38px;
            height: 38px;
            border-radius: 10px;
            background: linear-gradient(135deg, var(--accent-primary), var(--cyan));
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 19px;
            color: #FFF;
            box-shadow: 0 0 20px var(--accent-glow);
        }
        .brand-name {
            font-family: 'Outfit', sans-serif;
            font-size: 22px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text-main);
        }
        .brand-badge {
            font-size: 10px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 20px;
            background: rgba(99, 102, 241, 0.15);
            color: #A5B4FC;
            border: 1px solid rgba(99, 102, 241, 0.3);
            letter-spacing: 0.05em;
        }
        .nav-cta {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 22px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 600;
            text-decoration: none;
            background: linear-gradient(135deg, var(--accent-primary), #4F46E5);
            color: #FFF;
            box-shadow: 0 4px 18px var(--accent-glow);
            transition: all 0.25s ease;
        }
        .nav-cta:hover { transform: translateY(-2px); box-shadow: 0 6px 24px var(--accent-glow); }

        /* Hero */
        .hero {
            position: relative;
            z-index: 10;
            max-width: 1200px;
            margin: 0 auto;
            padding: 160px 24px 80px;
            text-align: center;
        }
        .pill-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 16px;
            border-radius: 30px;
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.3);
            font-size: 12px;
            font-weight: 600;
            color: #C7D2FE;
            margin-bottom: 24px;
        }
        .hero-title {
            font-family: 'Outfit', sans-serif;
            font-size: 64px;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: -0.03em;
            margin-bottom: 24px;
            max-width: 950px;
            margin-left: auto;
            margin-right: auto;
        }
        .gradient-text {
            background: linear-gradient(135deg, #FFFFFF 30%, #A5B4FC 70%, var(--cyan) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .hero-subtitle {
            font-size: 18px;
            color: var(--text-sub);
            max-width: 680px;
            margin: 0 auto 40px;
            font-weight: 400;
        }
        .cta-group {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 16px;
        }
        .btn-large {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            padding: 16px 36px;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 700;
            text-decoration: none;
            background: linear-gradient(135deg, var(--accent-primary), #4338CA);
            color: #FFF;
            box-shadow: 0 10px 30px var(--accent-glow);
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .btn-large:hover {
            transform: translateY(-3px) scale(1.02);
            box-shadow: 0 14px 40px var(--accent-glow);
        }
        .btn-secondary {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 16px 28px;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            text-decoration: none;
            background: var(--surface-2);
            color: var(--text-main);
            border: 1px solid var(--border);
            transition: all 0.2s;
        }
        .btn-secondary:hover { background: var(--surface-3); border-color: var(--border-hover); }

        /* Live Preview Simulation Card */
        .preview-container {
            max-width: 900px;
            margin: 60px auto 0;
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(16px);
            text-align: left;
        }
        .preview-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 20px;
        }
        .preview-dots { display: flex; gap: 6px; }
        .p-dot { width: 10px; height: 10px; border-radius: 50%; }
        .dot-r { background: #EF4444; } .dot-y { background: #F59E0B; } .dot-g { background: #10B981; }
        .preview-email {
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px 20px;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .badge-pill {
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .badge-high { background: rgba(239, 68, 68, 0.15); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-important { background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-draft { background: rgba(16, 185, 129, 0.15); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.3); }

        /* Feature Pillars */
        .features {
            max-width: 1200px;
            margin: 80px auto;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 28px;
            padding: 0 24px;
        }
        .feature-card {
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 32px 28px;
            transition: all 0.3s;
        }
        .feature-card:hover {
            border-color: var(--border-hover);
            transform: translateY(-4px);
            box-shadow: 0 12px 30px rgba(0,0,0,0.4);
        }
        .feature-icon {
            font-size: 28px;
            margin-bottom: 18px;
            display: inline-block;
        }
        .feature-title {
            font-family: 'Outfit', sans-serif;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 10px;
        }
        .feature-desc {
            font-size: 14px;
            color: var(--text-sub);
            line-height: 1.6;
        }

        footer {
            border-top: 1px solid var(--border);
            padding: 40px 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: var(--text-dim);
            font-size: 13px;
        }
        @media (max-width: 768px) {
            nav { padding: 16px 20px; }
            .hero-title { font-size: 40px; }
            .features { grid-template-columns: 1fr; }
            .cta-group { flex-direction: column; }
            footer { flex-direction: column; gap: 16px; text-align: center; }
        }
    </style>
</head>
<body>
    <div class="ambient-glow glow-1"></div>
    <div class="ambient-glow glow-2"></div>

    <nav>
        <a href="/" class="brand">
            <div class="brand-icon">N</div>
            <span class="brand-name">NEXUS</span>
            <span class="brand-badge">PRO EDITION</span>
        </a>
        <a href="/connect" class="nav-cta">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12.545,10.239v3.821h5.445c-0.712,2.315-2.647,3.972-5.445,3.972c-3.332,0-6.033-2.701-6.033-6.032s2.701-6.032,6.033-6.032c1.498,0,2.866,0.549,3.921,1.453l2.814-2.814C17.503,2.988,15.139,2,12.545,2C7.021,2,2.543,6.477,2.543,12s4.478,10,10.002,10c8.396,0,10.249-7.85,9.426-11.748L12.545,10.239z"/></svg>
            Connect Gmail
        </a>
    </nav>

    <main class="hero">
        <div class="pill-badge">
            <span style="color:var(--emerald);">●</span> AUTONOMOUS INBOX INTELLIGENCE ENGINE
        </div>
        <h1 class="hero-title">
            Your Inbox, <span class="gradient-text">Triaged & Answered</span> While You Sleep.
        </h1>
        <p class="hero-subtitle">
            NEXUS automatically classifies incoming emails, detects critical deadlines, and prepares high-caliber draft replies speaking in your personal voice.
        </p>

        <div class="cta-group">
            <a href="/connect" class="btn-large">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12.545,10.239v3.821h5.445c-0.712,2.315-2.647,3.972-5.445,3.972c-3.332,0-6.033-2.701-6.033-6.032s2.701-6.032,6.033-6.032c1.498,0,2.866,0.549,3.921,1.453l2.814-2.814C17.503,2.988,15.139,2,12.545,2C7.021,2,2.543,6.477,2.543,12s4.478,10,10.002,10c8.396,0,10.249-7.85,9.426-11.748L12.545,10.239z"/></svg>
                Launch with Google Workspace
            </a>
            <a href="#features" class="btn-secondary">Explore Capabilities ↓</a>
        </div>

        <!-- Live Simulation Card -->
        <div class="preview-container">
            <div class="preview-header">
                <div class="preview-dots">
                    <div class="p-dot dot-r"></div>
                    <div class="p-dot dot-y"></div>
                    <div class="p-dot dot-g"></div>
                </div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-dim);">
                    NEXUS Core 3.0 • Autonomous Engine Active
                </div>
            </div>

            <div class="preview-email">
                <div>
                    <div style="font-weight:600;font-size:14.5px;margin-bottom:3px;">Senior Software Engineer — Final Interview Schedule</div>
                    <div style="font-size:12px;color:var(--text-sub);font-family:'JetBrains Mono',monospace;">From: talent@hypertech.com</div>
                </div>
                <div style="display:flex;gap:8px;">
                    <span class="badge-pill badge-high">High Priority</span>
                    <span class="badge-pill badge-draft">Draft Prepared</span>
                </div>
            </div>

            <div class="preview-email" style="opacity:0.75;">
                <div>
                    <div style="font-weight:600;font-size:14.5px;margin-bottom:3px;">Q3 Financial Projections & Board Deck Review</div>
                    <div style="font-size:12px;color:var(--text-sub);font-family:'JetBrains Mono',monospace;">From: cfo@venturegroup.co</div>
                </div>
                <div style="display:flex;gap:8px;">
                    <span class="badge-pill badge-important">Important</span>
                    <span class="badge-pill badge-draft">Draft Prepared</span>
                </div>
            </div>
        </div>
    </main>

    <section class="features" id="features">
        <div class="feature-card">
            <div class="feature-icon">⚡</div>
            <h3 class="feature-title">Real-Time Autonomous Triage</h3>
            <p class="feature-desc">NEXUS operates 24/7 in the background, intercepting incoming mail within seconds. You never need to manually press refresh.</p>
        </div>
        <div class="feature-card">
            <div class="feature-icon">🛡️</div>
            <h3 class="feature-title">Safe "Draft First" Mode</h3>
            <p class="feature-desc">Replies are prepared directly inside your Gmail Drafts folder for one-click review. No unwanted auto-sends without explicit authorization.</p>
        </div>
        <div class="feature-card">
            <div class="feature-icon">👑</div>
            <h3 class="feature-title">Multi-Tenant Vault Isolation</h3>
            <p class="feature-desc">Complete cryptographic isolation. Your inbox data and replies belong strictly to your account, with dedicated Owner administration oversight.</p>
        </div>
    </section>

    <footer>
        <div>NEXUS AI Platform • All Rights Reserved.</div>
        <div>Created & Owned by Reshma Kodavali • OpenEnv Production Release</div>
    </footer>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# 2. DASHBOARD PAGE (World-Class Interactive Executive Dashboard with Live Poll)
# ══════════════════════════════════════════════════════════════════════════════
DASHBOARD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS — Inbox Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-void: #06080F;
            --surface-1: #0D111D;
            --surface-2: #141A2D;
            --surface-3: #1B233D;
            --accent: #6366F1;
            --accent-glow: rgba(99, 102, 241, 0.35);
            --cyan: #06B6D4;
            --emerald: #10B981;
            --amber: #F59E0B;
            --rose: #F43F5E;
            --border: rgba(99, 102, 241, 0.16);
            --border-strong: rgba(99, 102, 241, 0.3);
            --text-main: #F8FAFC;
            --text-sub: #94A3B8;
            --text-muted: #64748B;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-void);
            color: var(--text-main);
            min-height: 100vh;
            overflow-x: hidden;
        }
        .layout {
            display: grid;
            grid-template-columns: 280px 1fr;
            min-height: 100vh;
        }

        /* ── Sidebar ── */
        .sidebar {
            background: var(--surface-1);
            border-right: 1px solid var(--border);
            padding: 32px 24px;
            display: flex;
            flex-direction: column;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 40px;
            text-decoration: none;
        }
        .brand-icon {
            width: 36px; height: 36px;
            border-radius: 9px;
            background: linear-gradient(135deg, var(--accent), var(--cyan));
            display: flex;
            align-items: center; justify-content: center;
            font-family: 'Outfit', sans-serif;
            font-weight: 800; font-size: 18px; color: #FFF;
        }
        .brand-text {
            font-family: 'Outfit', sans-serif;
            font-size: 20px; font-weight: 700; color: var(--text-main);
        }
        .sidebar-section-title {
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: var(--text-muted);
            margin-bottom: 12px;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 500;
            color: var(--text-sub);
            text-decoration: none;
            cursor: pointer;
            border: 1px solid transparent;
            background: transparent;
            width: 100%;
            text-align: left;
            transition: all 0.2s;
            margin-bottom: 6px;
        }
        .nav-item:hover { background: var(--surface-2); color: var(--text-main); }
        .nav-item.active {
            background: rgba(99, 102, 241, 0.14);
            border-color: rgba(99, 102, 241, 0.3);
            color: #C7D2FE;
            font-weight: 600;
        }

        .sidebar-user {
            margin-top: auto;
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .avatar {
            width: 36px; height: 36px;
            border-radius: 50%;
            object-fit: cover;
            border: 1.5px solid var(--accent);
            background: var(--surface-3);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; color: #C7D2FE;
        }
        .user-details { flex: 1; min-width: 0; }
        .user-name {
            font-size: 13px; font-weight: 600; color: var(--text-main);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .user-role {
            font-size: 11px; color: var(--emerald);
            display: flex; align-items: center; gap: 4px;
        }

        /* ── Main Dashboard ── */
        .main {
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            max-height: 100vh;
        }
        .topbar {
            padding: 24px 44px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(6, 8, 15, 0.7);
            backdrop-filter: blur(16px);
            position: sticky; top: 0; z-index: 50;
        }
        .live-status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 20px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.28);
            font-size: 12px;
            color: #34D399;
            font-weight: 600;
        }
        .pulse-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            background: #10B981;
            box-shadow: 0 0 10px #10B981;
            animation: pulse-green 1.8s infinite;
        }
        @keyframes pulse-green {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.3); }
        }
        .action-cluster {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .btn-sync {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            background: linear-gradient(135deg, var(--accent), #4338CA);
            color: #FFF;
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 16px var(--accent-glow);
            transition: all 0.2s;
        }
        .btn-sync:hover { transform: translateY(-1px); box-shadow: 0 6px 20px var(--accent-glow); }

        .content {
            padding: 36px 44px;
        }

        /* ── Metric Cards ── */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 36px;
        }
        .m-card {
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 22px 24px;
            position: relative;
            overflow: hidden;
            transition: all 0.25s;
        }
        .m-card:hover { border-color: var(--border-strong); transform: translateY(-2px); }
        .m-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .m-val {
            font-family: 'Outfit', sans-serif;
            font-size: 34px;
            font-weight: 700;
            color: var(--text-main);
        }
        .m-sub {
            font-size: 11.5px;
            color: var(--text-sub);
            margin-top: 4px;
        }

        /* ── Feed Section ── */
        .feed-container {
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 14px;
            overflow: hidden;
        }
        .feed-header {
            padding: 20px 28px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .feed-title {
            font-family: 'Outfit', sans-serif;
            font-size: 18px;
            font-weight: 600;
        }
        .feed-controls {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .search-input {
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 7px 14px;
            font-size: 12.5px;
            color: var(--text-main);
            outline: none;
            width: 200px;
        }
        .search-input:focus { border-color: var(--cyan); }

        /* Email Rows */
        .email-list { display: flex; flex-direction: column; }
        .email-row {
            padding: 18px 28px;
            border-bottom: 1px solid rgba(99, 102, 241, 0.08);
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.15s;
        }
        .email-row:last-child { border-bottom: none; }
        .email-row:hover { background: rgba(99, 102, 241, 0.04); }
        .email-main-info { flex: 1; min-width: 0; padding-right: 24px; }
        .email-subject {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 4px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .email-sender {
            font-size: 12px;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-sub);
        }
        .badge-tag {
            padding: 3px 9px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .b-high { background: rgba(244, 63, 94, 0.15); color: #FB7185; border: 1px solid rgba(244, 63, 94, 0.3); }
        .b-medium { background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .b-low { background: rgba(148, 163, 184, 0.1); color: #94A3B8; border: 1px solid var(--border); }
        .b-draft { background: rgba(16, 185, 129, 0.15); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .b-spam { background: rgba(244, 63, 94, 0.12); color: #F43F5E; }

        /* Slide-over Drawer Modal */
        .drawer-overlay {
            position: fixed; inset: 0;
            background: rgba(0,0,0,0.65);
            backdrop-filter: blur(8px);
            z-index: 200;
            display: none;
            opacity: 0;
            transition: opacity 0.25s ease;
        }
        .drawer-overlay.active { display: block; opacity: 1; }
        .drawer {
            position: fixed; top: 0; right: 0; bottom: 0;
            width: 540px;
            max-width: 90vw;
            background: var(--surface-1);
            border-left: 1px solid var(--border-strong);
            box-shadow: -10px 0 40px rgba(0,0,0,0.8);
            z-index: 201;
            padding: 36px 32px;
            display: flex;
            flex-direction: column;
            transform: translateX(100%);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            overflow-y: auto;
        }
        .drawer.active { transform: translateX(0); }
        .drawer-close {
            align-self: flex-end;
            background: none; border: none;
            color: var(--text-muted);
            font-size: 20px; cursor: pointer;
        }
        .reply-box {
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            font-size: 13.5px;
            line-height: 1.6;
            margin-top: 16px;
            color: #E2E8F0;
        }
        @media (max-width: 900px) {
            .layout { grid-template-columns: 1fr; }
            .sidebar { display: none; }
            .metrics-grid { grid-template-columns: 1fr 1fr; }
            .topbar, .content { padding: 20px; }
        }
    </style>
</head>
<body>
    <div class="layout">
        <!-- Sidebar -->
        <aside class="sidebar">
            <a href="/" class="brand">
                <div class="brand-icon">N</div>
                <span class="brand-text">NEXUS</span>
            </a>

            <div class="sidebar-section-title">Navigation</div>
            <button class="nav-item active">
                <span>◈</span> Inbox Intelligence
            </button>
            <button class="nav-item" onclick="triggerManualSync()">
                <span>⚡</span> Scan Inbox Now
            </button>

            {% if is_owner %}
            <div style="margin-top: 14px;">
                <div class="sidebar-section-title">Platform Governance</div>
                <a href="/admin" class="nav-item" style="color:var(--cyan);background:rgba(6,182,212,0.08);border-color:rgba(6,182,212,0.25);">
                    <span>👑</span> Owner Command Center
                </a>
            </div>
            {% endif %}

            <!-- User Badge -->
            <div class="sidebar-user">
                {% if user.picture %}
                <img src="{{ user.picture }}" alt="{{ user.name }}" class="avatar">
                {% else %}
                <div class="avatar">{{ (user.name or user.email or 'U')[0].upper() }}</div>
                {% endif %}
                <div class="user-details">
                    <div class="user-name" title="{{ user.email }}">{{ user.name or 'My Account' }}</div>
                    <div class="user-role">{{ '👑 Platform Owner' if is_owner else '● Connected' }}</div>
                </div>
                <a href="/disconnect" style="color:var(--text-muted);text-decoration:none;font-size:14px;padding:4px;" title="Sign Out">✕</a>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="main">
            <header class="topbar">
                <div class="live-status-pill">
                    <div class="pulse-dot"></div>
                    <span>Autonomous Engine Active</span>
                    <span style="font-family:'JetBrains Mono',monospace;color:var(--text-muted);font-size:11px;">• auto-syncing</span>
                </div>

                <div class="action-cluster">
                    <div style="font-size:12px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;">
                        Next scan: <span id="countdown">15</span>s
                    </div>
                    <button class="btn-sync" id="syncBtn" onclick="triggerManualSync()">
                        <span>⚡</span> Force Sync
                    </button>
                </div>
            </header>

            <div class="content">
                <!-- Metrics -->
                <div class="metrics-grid">
                    <div class="m-card">
                        <div class="m-label">Total Emails Triaged</div>
                        <div class="m-val" id="metricTotal">—</div>
                        <div class="m-sub">inbox messages</div>
                    </div>
                    <div class="m-card">
                        <div class="m-label">Draft Replies Generated</div>
                        <div class="m-val" id="metricReplies" style="color:var(--emerald);">—</div>
                        <div class="m-sub">ready in Gmail Drafts</div>
                    </div>
                    <div class="m-card">
                        <div class="m-label">High-Priority Detected</div>
                        <div class="m-val" id="metricHigh" style="color:var(--amber);">—</div>
                        <div class="m-sub">urgent action items</div>
                    </div>
                    <div class="m-card">
                        <div class="m-label">Spam & Phishing Blocked</div>
                        <div class="m-val" id="metricSpam" style="color:var(--rose);">—</div>
                        <div class="m-sub">deflected automatically</div>
                    </div>
                </div>

                <!-- Feed Container -->
                <div class="feed-container">
                    <div class="feed-header">
                        <div class="feed-title">Live Autonomous Activity Stream</div>
                        <div class="feed-controls">
                            <input type="text" class="search-input" id="searchInput" placeholder="Filter by subject or sender..." oninput="filterEmails()">
                        </div>
                    </div>

                    <div class="email-list" id="emailList">
                        <div style="padding: 40px; text-align: center; color: var(--text-muted);">
                            Analyzing inbox stream...
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <!-- Slide-over Drawer for Email Inspection -->
    <div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>
    <aside class="drawer" id="drawer">
        <button class="drawer-close" onclick="closeDrawer()">✕</button>
        <div style="margin-top: 12px; margin-bottom: 20px;">
            <div id="drawerBadges" style="display:flex;gap:8px;margin-bottom:12px;"></div>
            <h2 id="drawerSubject" style="font-family:'Outfit',sans-serif;font-size:22px;margin-bottom:8px;color:var(--text-main);"></h2>
            <div id="drawerSender" style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-sub);"></div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:18px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;color:var(--cyan);margin-bottom:6px;">
                NEXUS AI Triage Analysis
            </div>
            <p id="drawerAnalysis" style="font-size:13.5px;color:var(--text-sub);line-height:1.6;"></p>
        </div>

        <div id="drawerReplySection" style="margin-top:24px;border-top:1px solid var(--border);padding-top:18px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;color:var(--emerald);">
                    Generated Response (Saved in Gmail Drafts)
                </span>
                <button onclick="copyReplyText()" style="background:none;border:none;color:var(--cyan);font-size:12px;cursor:pointer;font-weight:600;">
                    Copy Text ⎘
                </button>
            </div>
            <div class="reply-box" id="drawerReplyText"></div>
            <a href="https://mail.google.com/mail/u/0/#drafts" target="_blank" style="display:inline-block;margin-top:12px;font-size:12.5px;color:var(--cyan);text-decoration:none;">
                Open in Gmail Drafts ↗
            </a>
        </div>
    </aside>

    <script>
        let allEmails = [];
        let countdownSec = 15;

        // Auto-refresh timer countdown (continuous background sync)
        setInterval(() => {
            countdownSec--;
            if (countdownSec <= 0) {
                countdownSec = 15;
                fetchLiveFeed(true);
            }
            const cdEl = document.getElementById('countdown');
            if (cdEl) cdEl.textContent = countdownSec;
        }, 1000);

        async function fetchLiveFeed(sync = false) {
            try {
                const url = sync ? '/api/live_feed?sync=1' : '/api/live_feed';
                const res = await fetch(url);
                if (!res.ok) return;
                const data = await res.json();
                
                // Update metrics
                document.getElementById('metricTotal').textContent = data.stats.total || 0;
                document.getElementById('metricReplies').textContent = data.stats.replies || 0;
                document.getElementById('metricHigh').textContent = data.stats.high_priority || 0;
                document.getElementById('metricSpam').textContent = data.stats.spam || 0;

                allEmails = data.emails || [];
                renderEmailList(allEmails);
            } catch(e) {
                console.error('Auto-refresh error:', e);
            }
        }

        function renderEmailList(emails) {
            const container = document.getElementById('emailList');
            if (!emails.length) {
                container.innerHTML = `
                    <div style="padding: 60px 20px; text-align: center; color: var(--text-muted);">
                        <div style="font-size:24px;margin-bottom:8px;">✦</div>
                        <div style="font-weight:600;font-size:15px;color:var(--text-sub);">Inbox Synced & Up to Date</div>
                        <div style="font-size:13px;margin-top:4px;">New incoming emails will automatically appear here.</div>
                    </div>
                `;
                return;
            }

            container.innerHTML = emails.map((e, idx) => `
                <div class="email-row" onclick="openDrawer(${idx})">
                    <div class="email-main-info">
                        <div class="email-subject">${escapeHtml(e.subject || '(No Subject)')}</div>
                        <div class="email-sender">${escapeHtml(e.sender || 'unknown')}</div>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span class="badge-tag ${e.priority === 'high' ? 'b-high' : (e.priority === 'medium' ? 'b-medium' : 'b-low')}">${e.priority || 'low'}</span>
                        <span class="badge-tag" style="background:rgba(255,255,255,0.06);color:var(--text-sub);">${e.classification || 'normal'}</span>
                        ${e.reply_sent ? '<span class="badge-tag b-draft">✓ Drafted</span>' : ''}
                    </div>
                </div>
            `).join('');
        }

        function openDrawer(index) {
            const email = allEmails[index];
            if (!email) return;

            document.getElementById('drawerSubject').textContent = email.subject;
            document.getElementById('drawerSender').textContent = 'From: ' + email.sender;
            
            const badges = document.getElementById('drawerBadges');
            badges.innerHTML = `
                <span class="badge-tag ${email.priority === 'high' ? 'b-high' : 'b-medium'}">${email.priority} Priority</span>
                <span class="badge-tag" style="background:rgba(255,255,255,0.08);color:var(--text-sub);">${email.classification}</span>
            `;

            document.getElementById('drawerAnalysis').textContent = 
                `Classified as ${email.classification.toUpperCase()} with ${email.priority.toUpperCase()} urgency priority. Action timestamp: ${email.received_at || 'Just now'}.`;

            const replySection = document.getElementById('drawerReplySection');
            if (email.reply_sent && email.reply_text) {
                replySection.style.display = 'block';
                document.getElementById('drawerReplyText').textContent = email.reply_text;
            } else {
                replySection.style.display = 'none';
            }

            document.getElementById('drawerOverlay').classList.add('active');
            document.getElementById('drawer').classList.add('active');
        }

        function closeDrawer() {
            document.getElementById('drawerOverlay').classList.remove('active');
            document.getElementById('drawer').classList.remove('active');
        }

        function copyReplyText() {
            const txt = document.getElementById('drawerReplyText').textContent;
            navigator.clipboard.writeText(txt);
            alert('Reply copied to clipboard!');
        }

        function filterEmails() {
            const q = document.getElementById('searchInput').value.toLowerCase();
            const filtered = allEmails.filter(e => 
                (e.subject || '').toLowerCase().includes(q) || 
                (e.sender || '').toLowerCase().includes(q)
            );
            renderEmailList(filtered);
        }

        async function triggerManualSync() {
            const btn = document.getElementById('syncBtn');
            btn.disabled = true;
            btn.innerHTML = 'Scanning...';
            try {
                countdownSec = 15;
                await fetchLiveFeed(true);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>⚡</span> Force Sync';
            }
        }

        function escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // Initial load on start — immediately fetches and synchronizes new incoming emails
        fetchLiveFeed(true);
    </script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# 3. OWNER / ADMIN PORTAL (Executive Platform Governance)
# ══════════════════════════════════════════════════════════════════════════════
ADMIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS — Owner Command Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-void: #06080F;
            --surface-1: #0D111D;
            --surface-2: #141A2D;
            --accent: #6366F1;
            --cyan: #06B6D4;
            --emerald: #10B981;
            --amber: #F59E0B;
            --rose: #F43F5E;
            --border: rgba(99, 102, 241, 0.16);
            --text-main: #F8FAFC;
            --text-sub: #94A3B8;
            --text-muted: #64748B;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-void);
            color: var(--text-main);
            min-height: 100vh;
            padding: 32px 48px;
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 32px;
        }
        .brand-title {
            font-family: 'Outfit', sans-serif;
            font-size: 26px;
            font-weight: 700;
        }
        .btn-link {
            padding: 8px 18px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            background: var(--surface-2);
            color: var(--text-main);
            border: 1px solid var(--border);
            transition: all 0.2s;
        }
        .btn-link:hover { border-color: var(--cyan); }
        .grid-4 {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 36px;
        }
        .card {
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 22px 24px;
        }
        .card-table {
            background: var(--surface-1);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 32px;
        }
        .table-header {
            padding: 18px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-family: 'Outfit', sans-serif;
            font-size: 17px;
            font-weight: 600;
        }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 12px 24px; font-size: 11px; text-transform: uppercase; color: var(--text-muted); background: var(--surface-2); }
        td { padding: 14px 24px; border-bottom: 1px solid rgba(99,102,241,0.06); }
        tr:last-child td { border-bottom: none; }
        .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-sub); }
        .badge {
            padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; text-transform: uppercase;
        }
        .b-owner { background: rgba(99,102,241,0.2); color: #C7D2FE; border: 1px solid var(--accent); }
    </style>
</head>
<body>
    <header class="header">
        <div>
            <div class="brand-title">👑 NEXUS Platform Owner Command Center</div>
            <div style="font-size:12.5px;color:var(--text-sub);margin-top:4px;">
                Administrator: <span style="color:var(--cyan);font-weight:600;">{{ owner.email }}</span>
            </div>
        </div>
        <div style="display:flex;gap:12px;">
            <a href="/dashboard" class="btn-link">← Return to Dashboard</a>
            <a href="/disconnect" class="btn-link" style="color:var(--rose);">Log Out</a>
        </div>
    </header>

    <div class="grid-4">
        <div class="card">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:8px;">Registered Users</div>
            <div style="font-family:'Outfit',sans-serif;font-size:32px;font-weight:700;">{{ total_users }}</div>
            <div style="font-size:12px;color:var(--text-sub);margin-top:4px;">Connected accounts</div>
        </div>
        <div class="card">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:8px;">Platform Emails Triaged</div>
            <div style="font-family:'Outfit',sans-serif;font-size:32px;font-weight:700;color:var(--cyan);">{{ total_emails }}</div>
            <div style="font-size:12px;color:var(--text-sub);margin-top:4px;">Total analyzed</div>
        </div>
        <div class="card">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:8px;">AI Replies Prepared</div>
            <div style="font-family:'Outfit',sans-serif;font-size:32px;font-weight:700;color:var(--emerald);">{{ total_replies }}</div>
            <div style="font-size:12px;color:var(--text-sub);margin-top:4px;">Drafts & sends</div>
        </div>
        <div class="card">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:8px;">Spam Blocked</div>
            <div style="font-family:'Outfit',sans-serif;font-size:32px;font-weight:700;color:var(--rose);">{{ total_spam }}</div>
            <div style="font-size:12px;color:var(--text-sub);margin-top:4px;">Shielded</div>
        </div>
    </div>

    <!-- Registered Users -->
    <div class="card-table">
        <div class="table-header">
            <span>Registered Users Directory</span>
            <span style="font-size:12px;color:var(--emerald);font-weight:600;">● Active Deployment</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>User</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Reply Mode</th>
                    <th>Connected At</th>
                </tr>
            </thead>
            <tbody>
                {% for u in users %}
                <tr>
                    <td style="font-weight:600;">{{ u.name or 'User' }}</td>
                    <td class="mono">{{ u.email }}</td>
                    <td>
                        {% if u.email == owner.email or u.role == 'owner' %}
                        <span class="badge b-owner">👑 Owner</span>
                        {% else %}
                        <span class="badge" style="background:rgba(255,255,255,0.06);color:var(--text-sub);">User</span>
                        {% endif %}
                    </td>
                    <td class="mono">{{ u.reply_mode or 'draft' }}</td>
                    <td class="mono">{{ (u.connected_at or '—')[:16] }}</td>
                </tr>
                {% else %}
                <tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:30px;">No registered users yet.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Global Activity Stream -->
    <div class="card-table">
        <div class="table-header">
            <span>Live Platform Activity Stream (Last 15 Actions)</span>
            <span style="font-size:12px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;">Engine: {{ llm_model }}</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Account</th>
                    <th>Sender</th>
                    <th>Subject</th>
                    <th>Classification</th>
                    <th>Priority</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {% for a in activity %}
                <tr>
                    <td class="mono" style="color:var(--cyan);">{{ a.user_email }}</td>
                    <td class="mono">{{ (a.sender or '')[:24] }}</td>
                    <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ a.subject }}</td>
                    <td><span class="badge" style="background:rgba(255,255,255,0.06);color:var(--text-sub);">{{ a.classification }}</span></td>
                    <td><span class="badge" style="background:rgba(255,255,255,0.06);color:var(--text-sub);">{{ a.priority }}</span></td>
                    <td>
                        {% if a.reply_sent %}
                        <span style="color:var(--emerald);font-weight:600;font-size:12px;">✓ {{ a.status or 'Drafted' }}</span>
                        {% else %}
                        <span style="color:var(--text-muted);font-size:12px;">Classified</span>
                        {% endif %}
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:30px;">No triage stream yet.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# 4. ROUTES & API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if 'credentials' in session and 'user' in session:
        return redirect(url_for('dashboard'))
    return HOME_PAGE


@app.route("/connect")
def connect():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for('callback', _external=True)
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(auth_url)


@app.route("/callback")
def callback():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=session['state'],
        redirect_uri=url_for('callback', _external=True)
    )
    flow.code_verifier = session.get('code_verifier')
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session['credentials'] = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes)
    }
    session.permanent = True
    connected_at = int(_time.time())
    session['connected_at'] = connected_at

    # Discover user identity
    user_email = ""
    user_name  = ""
    user_pic   = ""
    try:
        gmail_service = build('gmail', 'v1', credentials=creds)
        prof = gmail_service.users().getProfile(userId='me').execute()
        user_email = prof.get('emailAddress', '').lower()
    except Exception as e:
        print(f"Notice: Gmail profile: {e}")

    try:
        oauth_service = build('oauth2', 'v2', credentials=creds)
        uinfo = oauth_service.userinfo().get().execute()
        user_name = uinfo.get('name') or uinfo.get('given_name') or ""
        user_pic  = uinfo.get('picture', '')
        if not user_email:
            user_email = uinfo.get('email', '').lower()
    except Exception:
        pass

    if not user_name and user_email:
        user_name = user_email.split('@')[0].capitalize()

    role = 'owner' if (user_email and user_email == OWNER_EMAIL) else 'user'
    session['user'] = {
        'email': user_email,
        'name': user_name,
        'picture': user_pic,
        'role': role,
        'reply_mode': 'draft'
    }

    with _db() as conn:
        conn.execute("""
            INSERT INTO users (email, name, picture, role, reply_mode, connected_at, last_active)
            VALUES (?, ?, ?, ?, 'draft', datetime('now'), datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                picture = excluded.picture,
                last_active = datetime('now')
        """, (user_email, user_name, user_pic, role))
        conn.commit()

    # Activate autonomous background polling loop
    with _agent_lock:
        _agent_state["credentials"]  = dict(session['credentials'])
        _agent_state["user_info"]    = dict(session['user'])
        _agent_state["connected_at"] = connected_at
        _agent_state["running"]      = True

    return redirect(url_for('dashboard'))


@app.route("/dashboard")
def dashboard():
    if 'credentials' not in session:
        return redirect(url_for('index'))

    user = session.get('user')
    if not user:
        creds_dict = session.get('credentials', {})
        user_email = ""
        try:
            from google.oauth2.credentials import Credentials as _C
            c = _C(**creds_dict)
            g = build('gmail', 'v1', credentials=c)
            user_email = g.users().getProfile(userId='me').execute().get('emailAddress', '').lower()
        except Exception:
            pass
        user = {
            'email': user_email,
            'name': user_email.split('@')[0].capitalize() if user_email else 'My Account',
            'picture': '',
            'role': 'owner' if user_email == OWNER_EMAIL else 'user',
            'reply_mode': 'draft'
        }
        session['user'] = user

    with _agent_lock:
        if not _agent_state.get("credentials"):
            _agent_state["credentials"]  = dict(session['credentials'])
            _agent_state["user_info"]    = dict(user)
            _agent_state["connected_at"] = session.get('connected_at', 0)
            _agent_state["running"]      = True

    is_owner = (user.get('role') == 'owner' or user.get('email', '').lower() == OWNER_EMAIL)
    return render_template_string(DASHBOARD_PAGE, user=user, is_owner=is_owner)


@app.route("/admin")
def admin_portal():
    user = session.get('user')
    if not user:
        return redirect(url_for('index'))
    if user.get('role') != 'owner' and user.get('email', '').lower() != OWNER_EMAIL:
        return """
        <div style="font-family:sans-serif;text-align:center;margin-top:120px;color:#F87171;">
            <h1 style="font-size:32px;">403 Forbidden: Owner Access Required</h1>
            <p style="color:#9A9189;margin-top:8px;">Only the platform owner can access the Administrator Command Center.</p>
            <p><a href="/dashboard" style="color:#6366F1;text-decoration:none;font-weight:600;">← Return to Dashboard</a></p>
        </div>
        """, 403

    with _db() as conn:
        total_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_emails  = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
        total_replies = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE reply_sent = 1").fetchone()[0]
        total_spam    = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE classification = 'spam'").fetchone()[0]
        users_list    = conn.execute("SELECT email, name, picture, role, reply_mode, connected_at, last_active FROM users ORDER BY connected_at DESC").fetchall()
        recent_act    = conn.execute("""
            SELECT user_email, subject, sender, classification, priority, reply_sent, status, received_at
            FROM processed_emails
            ORDER BY received_at DESC
            LIMIT 15
        """).fetchall()

    return render_template_string(ADMIN_PAGE,
        owner=user,
        total_users=total_users,
        total_emails=total_emails,
        total_replies=total_replies,
        total_spam=total_spam,
        users=[dict(u) for u in users_list],
        activity=[dict(a) for a in recent_act],
        llm_model=_LLM_MODEL
    )


@app.route("/api/live_feed")
def live_feed():
    """Real-time live feed API polled automatically by frontend every 5-15 seconds."""
    _ensure_background_thread()
    user = session.get('user', {})
    user_email = user.get('email', '').lower()
    if not user_email:
        return jsonify({"error": "Not authenticated"}), 401

    # Keep background state synchronized with current session credentials
    if 'credentials' in session:
        with _agent_lock:
            if not _agent_state.get("credentials"):
                _agent_state["credentials"]  = dict(session['credentials'])
                _agent_state["user_info"]    = dict(user)
                _agent_state["connected_at"] = session.get('connected_at', 0)
                _agent_state["running"]      = True

    # If requested via sync=1, immediately check for new unread inbox messages
    if request.args.get('sync') == '1' and 'credentials' in session:
        creds_dict   = dict(session['credentials'])
        connected_at = session.get('connected_at', 0)
        reply_mode   = user.get('reply_mode', 'draft')
        with _agent_lock:
            can_sync = (_agent_state.get("status") == "idle")
            if can_sync:
                _agent_state["status"]    = "polling"
                _agent_state["last_poll"] = datetime.now().isoformat(timespec='seconds')
        if can_sync:
            try:
                _process_inbox(creds_dict, connected_at, user_info=user, reply_mode=reply_mode)
            except Exception as e:
                print(f"Sync check notice: {e}")
            finally:
                with _agent_lock:
                    _agent_state["status"] = "idle"

    with _db() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]
        replies   = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND reply_sent=1", (user_email,)).fetchone()[0]
        high_pri  = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND priority='high'", (user_email,)).fetchone()[0]
        spam      = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND classification='spam'", (user_email,)).fetchone()[0]
        
        rows = conn.execute("""
            SELECT id, subject, sender, received_at, classification, priority, reply_sent, reply_text, status
            FROM processed_emails
            WHERE user_email = ?
            ORDER BY rowid DESC
            LIMIT 40
        """, (user_email,)).fetchall()

    with _agent_lock:
        status    = _agent_state.get("status", "idle")
        last_poll = _agent_state.get("last_poll")

    return jsonify({
        "stats": {
            "total": total,
            "replies": replies,
            "high_priority": high_pri,
            "spam": spam,
            "agent_status": status,
            "last_poll": last_poll
        },
        "emails": [dict(r) for r in rows]
    })


@app.route("/run")
def run_agent():
    """Manual on-demand trigger."""
    if 'credentials' not in session:
        return {"error": "Not connected"}, 401

    try:
        creds_dict   = dict(session['credentials'])
        connected_at = session.get('connected_at', 0)
        user         = session.get('user', {})
        user_email   = user.get('email', '').lower()
        reply_mode   = user.get('reply_mode', 'draft')

        with _db() as conn:
            before_count = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]

        _process_inbox(creds_dict, connected_at, user_info=user, reply_mode=reply_mode)

        with _db() as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]
            rows = conn.execute("""
                SELECT subject, sender, classification, priority, reply_sent, status
                FROM processed_emails
                WHERE user_email = ?
                ORDER BY rowid DESC
                LIMIT ?
            """, (user_email, max(1, after_count - before_count))).fetchall()

        emails = [{
            "subject": r["subject"],
            "sender": r["sender"],
            "classification": r["classification"],
            "priority": r["priority"],
            "reply_sent": bool(r["reply_sent"]),
            "status": r["status"]
        } for r in rows]

        return {
            "status": "success",
            "total": len(emails),
            "score": round(len(emails) * 0.8, 2),
            "emails": emails
        }

    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/stats")
def stats():
    user = session.get('user', {})
    user_email = user.get('email', '').lower()
    with _db() as conn:
        if user_email:
            total     = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]
            replies   = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND reply_sent=1", (user_email,)).fetchone()[0]
            high_pri  = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND priority='high'", (user_email,)).fetchone()[0]
            spam      = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ? AND classification='spam'", (user_email,)).fetchone()[0]
        else:
            total     = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
            replies   = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE reply_sent=1").fetchone()[0]
            high_pri  = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE priority='high'").fetchone()[0]
            spam      = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE classification='spam'").fetchone()[0]

    with _agent_lock:
        status    = _agent_state.get("status", "idle")
        last_poll = _agent_state.get("last_poll")

    return {
        "total_received":  total,
        "total_processed": total,
        "replies_sent":    replies,
        "high_priority":   high_pri,
        "spam_blocked":    spam,
        "agent_status":    status,
        "last_poll":       last_poll,
    }


@app.route("/replies")
def replies():
    user = session.get('user', {})
    user_email = user.get('email', '').lower()
    with _db() as conn:
        if user_email:
            rows = conn.execute("""
                SELECT subject, sender, received_at, classification, priority, reply_text, status
                FROM processed_emails
                WHERE user_email = ? AND reply_sent = 1
                ORDER BY received_at DESC
            """, (user_email,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT subject, sender, received_at, classification, priority, reply_text, status
                FROM processed_emails
                WHERE reply_sent = 1
                ORDER BY received_at DESC
            """).fetchall()
    return {"replies": [dict(r) for r in rows]}


@app.route("/disconnect")
def disconnect():
    with _agent_lock:
        _agent_state["credentials"]  = None
        _agent_state["user_info"]    = None
        _agent_state["connected_at"] = 0
        _agent_state["running"]      = False
        _agent_state["status"]       = "idle"
    session.clear()
    return redirect(url_for('index'))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
