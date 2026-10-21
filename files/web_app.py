"""
NEXUS AI — Autonomous Executive Email Intelligence
World-Class Multi-Tenant Platform with Light Cream & Medium Shy Brown Luxury Executive UI.
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
- If needs_reply is false, reply MUST be an empty string "".
- If needs_reply is true, craft an articulate, warm, and professional reply signed "{sig}".
- Never promise actions outside the user's explicit instructions.
"""

def _llm_triage(subject: str, sender: str, body: str, is_automated: bool, is_bulk: bool, user_name: str = "User", signature: str = None) -> dict:
    fallback = {
        "classification": "spam" if is_bulk else ("normal" if is_automated else "important"),
        "priority": "low" if (is_automated or is_bulk) else "medium",
        "needs_reply": False if (is_automated or is_bulk) else True,
        "reply": f"Hi,\n\nThank you for reaching out. I have received your email regarding '{subject}' and will review it shortly.\n\nBest regards,\n{signature or user_name}" if not (is_automated or is_bulk) else "",
        "reason": "Rule-based triage evaluation."
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
    "poll_interval": 15,      # Autonomous check every 15 seconds
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

    # Fetch latest 20 messages in INBOX
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
    """Runs forever in a daemon thread — autonomously polls inboxes every 15 seconds."""
    while True:
        with _agent_lock:
            creds        = _agent_state.get("credentials")
            connected_at = _agent_state.get("connected_at", 0)
            user_info    = _agent_state.get("user_info")
            interval     = _agent_state.get("poll_interval", 15)

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
# 1. LANDING PAGE (Light Cream & Medium Shy Brown Luxury Theme)
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
            /* Light Cream & Medium Shy Brown Luxury Palette */
            --bg-cream-base: #FBF8F3;
            --bg-cream-subtle: #F5EFE6;
            --bg-cream-card: #FFFFFF;
            --surface-cream-tint: #EFE7DC;
            --border-cream: #E6DDCE;

            /* Medium Shy Brown (Gentle, quiet, modest luxury hazelnut-taupe brown) */
            --brown-shy: #8C6D58;
            --brown-primary: #7A5C47;
            --brown-deep: #634835;
            --brown-light: #F2EBE4;
            --brown-border: #DCCEC2;
            --brown-glow: rgba(140, 109, 88, 0.18);

            /* Typography */
            --text-heading: #2A211B;
            --text-body: #4D3E35;
            --text-muted: #827165;
            --text-dim: #AC9E93;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-cream-base);
            color: var(--text-body);
            min-height: 100vh;
            overflow-x: hidden;
            line-height: 1.55;
        }
        /* Ambient Glow (Soft Cream and Delicate Shy Brown Mist) */
        .ambient-glow {
            position: fixed;
            width: 780px;
            height: 780px;
            border-radius: 50%;
            filter: blur(150px);
            pointer-events: none;
            z-index: 0;
            opacity: 0.40;
        }
        .glow-1 { top: -220px; left: 50%; transform: translateX(-50%); background: radial-gradient(circle, #EFE4D6 0%, #FAF2E6 100%); }
        .glow-2 { bottom: -140px; right: -100px; background: radial-gradient(circle, #E3D5C8 0%, #F5EDE4 70%); }

        nav {
            position: fixed;
            top: 0; left: 0; right: 0;
            z-index: 100;
            padding: 18px 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(251, 248, 243, 0.90);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border-cream);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
        }
        .brand-icon {
            width: 40px;
            height: 40px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--brown-shy), var(--brown-deep));
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 20px;
            color: #FFF;
            box-shadow: 0 4px 14px var(--brown-glow);
        }
        .brand-name {
            font-family: 'Outfit', sans-serif;
            font-size: 23px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text-heading);
        }
        .brand-badge {
            font-size: 10px;
            font-weight: 700;
            padding: 3px 9px;
            border-radius: 20px;
            background: var(--brown-light);
            color: var(--brown-primary);
            border: 1px solid var(--brown-border);
            letter-spacing: 0.05em;
        }
        .nav-cta {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 24px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            background: var(--brown-primary);
            color: #FFF;
            box-shadow: 0 4px 14px var(--brown-glow);
            transition: all 0.25s ease;
        }
        .nav-cta:hover { transform: translateY(-2px); background: var(--brown-deep); box-shadow: 0 6px 20px var(--brown-glow); }

        /* Hero */
        .hero {
            position: relative;
            z-index: 10;
            max-width: 1200px;
            margin: 0 auto;
            padding: 155px 24px 80px;
            text-align: center;
        }
        .pill-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 18px;
            border-radius: 30px;
            background: var(--brown-light);
            border: 1px solid var(--brown-border);
            font-size: 12.5px;
            font-weight: 600;
            color: var(--brown-primary);
            margin-bottom: 24px;
        }
        .hero-title {
            font-family: 'Outfit', sans-serif;
            font-size: 64px;
            font-weight: 800;
            line-height: 1.12;
            letter-spacing: -0.03em;
            margin-bottom: 24px;
            max-width: 960px;
            margin-left: auto;
            margin-right: auto;
            color: var(--text-heading);
        }
        .gradient-text {
            background: linear-gradient(135deg, #2A211B 20%, var(--brown-primary) 65%, #A27B5C 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .hero-subtitle {
            font-size: 18.5px;
            color: var(--text-muted);
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
            border-radius: 12px;
            font-size: 16px;
            font-weight: 700;
            text-decoration: none;
            background: linear-gradient(135deg, var(--brown-primary), var(--brown-deep));
            color: #FFF;
            box-shadow: 0 8px 24px var(--brown-glow);
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .btn-large:hover { transform: translateY(-3px); box-shadow: 0 12px 30px var(--brown-glow); background: var(--brown-deep); }
        .btn-outline {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 16px 30px;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 600;
            text-decoration: none;
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            color: var(--text-heading);
            transition: all 0.25s ease;
            box-shadow: 0 2px 8px rgba(122, 92, 71, 0.05);
        }
        .btn-outline:hover { border-color: var(--brown-shy); color: var(--brown-primary); transform: translateY(-2px); }

        /* Feature Matrix */
        .matrix-section {
            position: relative;
            z-index: 10;
            max-width: 1100px;
            margin: 40px auto 100px;
            padding: 0 24px;
        }
        .matrix-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 24px;
        }
        .matrix-card {
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 16px;
            padding: 36px 30px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 18px rgba(122, 92, 71, 0.05);
            position: relative;
        }
        .matrix-card:hover {
            border-color: var(--brown-border);
            transform: translateY(-4px);
            box-shadow: 0 12px 30px var(--brown-glow);
        }
        .matrix-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            background: var(--brown-light);
            border: 1px solid var(--brown-border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            margin-bottom: 20px;
            color: var(--brown-primary);
        }
        .matrix-title {
            font-family: 'Outfit', sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: var(--text-heading);
            margin-bottom: 10px;
        }
        .matrix-desc {
            font-size: 14.5px;
            color: var(--text-muted);
            line-height: 1.6;
        }

        footer {
            position: relative;
            z-index: 10;
            border-top: 1px solid var(--border-cream);
            padding: 36px 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13.5px;
            color: var(--text-muted);
            background: var(--bg-cream-subtle);
        }
        footer a { color: var(--brown-primary); text-decoration: none; font-weight: 600; }

        @media (max-width: 900px) {
            nav { padding: 16px 24px; }
            .hero-title { font-size: 42px; }
            .matrix-grid { grid-template-columns: 1fr; }
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
            <span class="brand-badge">ATELIER</span>
        </a>
        <div style="display:flex;align-items:center;gap:20px;">
            <a href="/login" class="nav-cta">Connect Gmail →</a>
        </div>
    </nav>

    <main class="hero">
        <div class="pill-badge">
            <span>✦</span>
            <span>Autonomous Executive Email Intelligence</span>
        </div>
        <h1 class="hero-title">
            Your Executive Inbox,<br>
            <span class="gradient-text">Triaged & Answered in Real Time.</span>
        </h1>
        <p class="hero-subtitle">
            NEXUS autonomously monitors your inbox, extracts critical action items, classifies urgency, and generates safe, personalized drafts without manual button clicks.
        </p>
        <div class="cta-group">
            <a href="/login" class="btn-large">
                <span>Connect with Google</span>
                <span>→</span>
            </a>
            <a href="#features" class="btn-outline">
                <span>Explore Capabilities</span>
            </a>
        </div>
    </main>

    <section class="matrix-section" id="features">
        <div class="matrix-grid">
            <div class="matrix-card">
                <div class="matrix-icon">⚡</div>
                <h3 class="matrix-title">Autonomous Live Sync</h3>
                <p class="matrix-desc">
                    Engine continuously scans in real time every 15 seconds. New emails are instantly analyzed and surfaced without manual refresh.
                </p>
            </div>
            <div class="matrix-card">
                <div class="matrix-icon">🛡️</div>
                <h3 class="matrix-title">Safe-by-Default Drafts</h3>
                <p class="matrix-desc">
                    Replies are crafted directly into your Gmail Drafts folder. You maintain 100% human-in-the-loop sign-off before any message goes out.
                </p>
            </div>
            <div class="matrix-card">
                <div class="matrix-icon">⚖️</div>
                <h3 class="matrix-title">Executive Triage Matrix</h3>
                <p class="matrix-desc">
                    High, Medium, and Low urgency classification instantly separates urgent client inquiries from newsletters and low-priority digests.
                </p>
            </div>
        </div>
    </section>

    <footer>
        <div>© 2026 NEXUS Intelligence Inc. • Light Cream & Medium Shy Brown Atelier Edition</div>
        <div>Platform Owner: <a href="/admin">Administrator Portal</a></div>
    </footer>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# 2. DASHBOARD PAGE (Light Cream & Medium Shy Brown Luxury Theme)
# ══════════════════════════════════════════════════════════════════════════════
DASHBOARD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS — Executive Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            /* Light Cream & Medium Shy Brown Luxury Palette */
            --bg-cream-base: #FBF8F3;
            --bg-cream-subtle: #F5EFE6;
            --bg-cream-card: #FFFFFF;
            --surface-cream-tint: #EFE7DC;
            --border-cream: #E6DDCE;

            /* Medium Shy Brown */
            --brown-shy: #8C6D58;
            --brown-primary: #7A5C47;
            --brown-deep: #634835;
            --brown-light: #F2EBE4;
            --brown-border: #DCCEC2;
            --brown-glow: rgba(140, 109, 88, 0.18);

            /* Typography */
            --text-heading: #2A211B;
            --text-body: #4D3E35;
            --text-muted: #827165;
            --text-dim: #AC9E93;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-cream-base);
            color: var(--text-body);
            min-height: 100vh;
            display: grid;
            grid-template-columns: 280px 1fr;
            overflow-x: hidden;
        }

        /* ── Left Sidebar ── */
        .sidebar {
            background: var(--bg-cream-subtle);
            border-right: 1px solid var(--border-cream);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            padding: 28px 22px;
            min-height: 100vh;
        }
        .brand-cluster {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 36px;
            text-decoration: none;
        }
        .brand-icon {
            width: 36px; height: 36px;
            border-radius: 10px;
            background: linear-gradient(135deg, var(--brown-shy), var(--brown-deep));
            display: flex; align-items: center; justify-content: center;
            font-family: 'Outfit', sans-serif; font-weight: 800; font-size: 18px; color: #FFF;
            box-shadow: 0 4px 12px var(--brown-glow);
        }
        .brand-name {
            font-family: 'Outfit', sans-serif;
            font-size: 21px;
            font-weight: 700;
            color: var(--text-heading);
        }
        .nav-links {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 11px 16px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            color: var(--text-muted);
            text-decoration: none;
            transition: all 0.2s ease;
        }
        .nav-item:hover {
            color: var(--text-heading);
            background: var(--surface-cream-tint);
        }
        .nav-item.active {
            color: var(--brown-primary);
            background: var(--brown-light);
            border: 1px solid var(--brown-border);
        }

        .user-card {
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 12px;
            padding: 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            box-shadow: 0 2px 8px rgba(122, 92, 71, 0.05);
        }
        .user-avatar {
            width: 42px; height: 42px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--brown-light), var(--surface-cream-tint));
            border: 1px solid var(--brown-border);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; color: var(--brown-primary);
            overflow: hidden;
            flex-shrink: 0;
        }
        .user-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .user-details { flex: 1; min-width: 0; }
        .user-name {
            font-size: 13.5px; font-weight: 700; color: var(--text-heading);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .user-role-badge {
            font-size: 10px; font-weight: 700; text-transform: uppercase;
            padding: 2px 7px; border-radius: 4px;
            background: var(--brown-light); color: var(--brown-primary);
            display: inline-block; margin-top: 3px;
        }

        /* ── Main Area ── */
        .main {
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            max-height: 100vh;
            background: var(--bg-cream-base);
        }
        .topbar {
            padding: 22px 44px;
            border-bottom: 1px solid var(--border-cream);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(251, 248, 243, 0.92);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            position: sticky; top: 0; z-index: 50;
        }
        .live-status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 16px;
            border-radius: 20px;
            background: var(--brown-light);
            border: 1px solid var(--brown-border);
            font-size: 12.5px;
            color: var(--brown-primary);
            font-weight: 600;
        }
        .pulse-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            background: var(--brown-primary);
            box-shadow: 0 0 10px var(--brown-primary);
            animation: pulse-shy-brown 1.8s infinite;
        }
        @keyframes pulse-shy-brown {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.35; transform: scale(1.35); }
        }
        .action-cluster {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .countdown-indicator {
            font-size: 12.5px;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
            background: var(--surface-cream-tint);
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid var(--border-cream);
        }
        .btn-sync {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 22px;
            border-radius: 10px;
            font-size: 13.5px;
            font-weight: 600;
            background: var(--brown-primary);
            color: #FFF;
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 14px var(--brown-glow);
            transition: all 0.2s;
        }
        .btn-sync:hover { transform: translateY(-1px); background: var(--brown-deep); box-shadow: 0 6px 20px var(--brown-glow); }

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
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 14px;
            padding: 22px 24px;
            position: relative;
            overflow: hidden;
            transition: all 0.25s;
            box-shadow: 0 3px 14px rgba(122, 92, 71, 0.04);
        }
        .m-card:hover { border-color: var(--brown-border); transform: translateY(-2px); box-shadow: 0 8px 24px var(--brown-glow); }
        .m-label {
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .m-val {
            font-family: 'Outfit', sans-serif;
            font-size: 36px;
            font-weight: 700;
            color: var(--text-heading);
        }
        .m-sub {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* ── Feed Section ── */
        .feed-container {
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(122, 92, 71, 0.04);
        }
        .feed-header {
            padding: 22px 28px;
            border-bottom: 1px solid var(--border-cream);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #FFFEFD;
        }
        .feed-title {
            font-family: 'Outfit', sans-serif;
            font-size: 19px;
            font-weight: 700;
            color: var(--text-heading);
        }
        .feed-controls {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .search-input {
            background: var(--surface-cream-tint);
            border: 1px solid var(--border-cream);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13px;
            color: var(--text-heading);
            outline: none;
            width: 240px;
            transition: all 0.2s;
        }
        .search-input:focus { border-color: var(--brown-shy); background: #FFF; box-shadow: 0 0 0 3px var(--brown-glow); }

        /* Email Rows */
        .email-list { display: flex; flex-direction: column; }
        .email-row {
            padding: 18px 28px;
            border-bottom: 1px solid var(--border-cream);
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.15s;
            background: #FFF;
        }
        .email-row:last-child { border-bottom: none; }
        .email-row:hover { background: var(--brown-light); }
        .email-main-info { flex: 1; min-width: 0; padding-right: 24px; }
        .email-subject {
            font-size: 14.5px;
            font-weight: 600;
            color: var(--text-heading);
            margin-bottom: 4px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .email-sender {
            font-size: 12.5px;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-muted);
        }
        .badge-tag {
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .b-high { background: #FDE8E4; color: #C84B31; border: 1px solid #F6CBC4; }
        .b-medium { background: #FAF1E4; color: #9A6B2F; border: 1px solid #EED9BE; }
        .b-low { background: var(--surface-cream-tint); color: var(--text-muted); border: 1px solid var(--border-cream); }
        .b-draft { background: #F2F7EA; color: #4D7C0F; border: 1px solid #D4E7BF; }
        .b-brown { background: var(--brown-light); color: var(--brown-primary); border: 1px solid var(--brown-border); }

        /* Slide-over Drawer Modal */
        .drawer-overlay {
            position: fixed; inset: 0;
            background: rgba(42, 33, 27, 0.45);
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            z-index: 200;
            display: none;
            opacity: 0;
            transition: opacity 0.25s ease;
        }
        .drawer-overlay.active { display: block; opacity: 1; }
        .drawer {
            position: fixed; top: 0; right: 0; bottom: 0;
            width: 560px;
            max-width: 92vw;
            background: #FFFFFF;
            border-left: 1px solid var(--border-cream);
            box-shadow: -10px 0 40px rgba(42, 33, 27, 0.15);
            z-index: 210;
            padding: 36px 32px;
            overflow-y: auto;
            transform: translateX(100%);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            display: flex; flex-direction: column;
        }
        .drawer.active { transform: translateX(0); }
        .drawer-close {
            position: absolute; top: 24px; right: 24px;
            background: var(--surface-cream-tint);
            border: 1px solid var(--border-cream);
            color: var(--text-muted);
            width: 34px; height: 34px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            display: flex; align-items: center; justify-content: center;
            transition: all 0.2s;
        }
        .drawer-close:hover { color: var(--text-heading); border-color: var(--brown-shy); background: var(--brown-light); }
        .drawer-subject {
            font-family: 'Outfit', sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: var(--text-heading);
            margin-bottom: 8px;
            padding-right: 40px;
        }
        .drawer-sender {
            font-size: 13px;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-muted);
            margin-bottom: 24px;
        }
        .drawer-card {
            background: var(--surface-cream-tint);
            border: 1px solid var(--border-cream);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .reply-box {
            background: var(--brown-light);
            border: 1px solid var(--brown-border);
            border-radius: 10px;
            padding: 16px 20px;
            font-size: 13.5px;
            line-height: 1.6;
            color: var(--text-heading);
            white-space: pre-wrap;
            font-family: 'Plus Jakarta Sans', sans-serif;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <!-- Left Navigation -->
    <aside class="sidebar">
        <div>
            <a href="/dashboard" class="brand-cluster">
                <div class="brand-icon">N</div>
                <div class="brand-name">NEXUS</div>
            </a>

            <nav class="nav-links">
                <a href="/dashboard" class="nav-item active">
                    <span>⚡</span> <span>Live Ingestion</span>
                </a>
                {% if is_owner %}
                <a href="/admin" class="nav-item">
                    <span>👑</span> <span>Owner Command</span>
                </a>
                {% endif %}
                <a href="https://mail.google.com" target="_blank" class="nav-item">
                    <span>↗</span> <span>Open Gmail</span>
                </a>
            </nav>
        </div>

        <div>
            <div class="user-card">
                <div class="user-avatar">
                    {% if user.picture %}
                    <img src="{{ user.picture }}" alt="User">
                    {% else %}
                    {{ user.name[:1] if user.name else 'U' }}
                    {% endif %}
                </div>
                <div class="user-details">
                    <div class="user-name">{{ user.name }}</div>
                    <div class="user-role-badge">{{ user.role|upper }}</div>
                </div>
                <a href="/disconnect" title="Disconnect" style="color:var(--text-muted);text-decoration:none;font-size:16px;">⎋</a>
            </div>
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
                <div class="countdown-indicator">
                    Next scan: <span id="countdown" style="font-weight:700;color:var(--brown-primary);">15</span>s
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
                    <div class="m-label">Total Ingested</div>
                    <div class="m-val" id="metricTotal">0</div>
                    <div class="m-sub">Monitored inbox messages</div>
                </div>
                <div class="m-card">
                    <div class="m-label">AI Drafts Prepared</div>
                    <div class="m-val" id="metricReplies" style="color:var(--brown-primary);">0</div>
                    <div class="m-sub">Safe drafts ready for sign-off</div>
                </div>
                <div class="m-card">
                    <div class="m-label">High Urgency</div>
                    <div class="m-val" id="metricHigh" style="color:#C84B31;">0</div>
                    <div class="m-sub">Urgent personal action items</div>
                </div>
                <div class="m-card">
                    <div class="m-label">Spam Blocked</div>
                    <div class="m-val" id="metricSpam" style="color:var(--brown-shy);">0</div>
                    <div class="m-sub">Deceptive & bulk filtered</div>
                </div>
            </div>

            <!-- Feed Section -->
            <div class="feed-container">
                <div class="feed-header">
                    <div class="feed-title">Real-Time Autonomous Feed</div>
                    <div class="feed-controls">
                        <input type="text" class="search-input" id="searchInput" placeholder="Search sender or subject..." oninput="filterEmails()">
                    </div>
                </div>

                <div class="email-list" id="emailList">
                    <div style="padding: 60px 20px; text-align: center; color: var(--text-muted);">
                        <div style="font-size:24px;margin-bottom:8px;color:var(--brown-primary);">✦</div>
                        <div style="font-weight:600;font-size:15px;color:var(--text-heading);">Inbox Synced & Up to Date</div>
                        <div style="font-size:13px;margin-top:4px;">New incoming emails will automatically stream here.</div>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <!-- Slide-over Drawer -->
    <div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>
    <aside class="drawer" id="drawer">
        <button class="drawer-close" onclick="closeDrawer()">✕</button>
        <div id="drawerBadges" style="display:flex;gap:8px;margin-bottom:14px;"></div>
        <h2 class="drawer-subject" id="drawerSubject">Subject Line</h2>
        <div class="drawer-sender" id="drawerSender">sender@example.com</div>

        <div class="drawer-card">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-muted);letter-spacing:0.08em;margin-bottom:6px;">
                Triage Diagnosis
            </div>
            <div id="drawerAnalysis" style="font-size:13.5px;color:var(--text-heading);line-height:1.5;"></div>
        </div>

        <div id="drawerReplySection" style="margin-top:10px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--brown-primary);letter-spacing:0.08em;">
                    Prepared Response Draft
                </div>
                <button onclick="copyReplyText()" style="background:none;border:none;color:var(--brown-primary);font-size:12.5px;cursor:pointer;font-weight:600;">
                    Copy Text ⎘
                </button>
            </div>
            <div class="reply-box" id="drawerReplyText"></div>
            <a href="https://mail.google.com/mail/u/0/#drafts" target="_blank" style="display:inline-block;margin-top:14px;font-size:13px;color:var(--brown-primary);font-weight:600;text-decoration:none;">
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
                        <div style="font-size:24px;margin-bottom:8px;color:var(--brown-primary);">✦</div>
                        <div style="font-weight:600;font-size:15px;color:var(--text-heading);">Inbox Synced & Up to Date</div>
                        <div style="font-size:13px;margin-top:4px;">New incoming emails will automatically stream here.</div>
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
                        <span class="badge-tag b-brown">${e.classification || 'normal'}</span>
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
                <span class="badge-tag b-brown">${email.classification}</span>
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
# 3. OWNER / ADMIN PORTAL (Light Cream & Medium Shy Brown Luxury Theme)
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
            /* Light Cream & Medium Shy Brown Luxury Palette */
            --bg-cream-base: #FBF8F3;
            --bg-cream-subtle: #F5EFE6;
            --bg-cream-card: #FFFFFF;
            --surface-cream-tint: #EFE7DC;
            --border-cream: #E6DDCE;

            /* Medium Shy Brown */
            --brown-shy: #8C6D58;
            --brown-primary: #7A5C47;
            --brown-deep: #634835;
            --brown-light: #F2EBE4;
            --brown-border: #DCCEC2;
            --brown-glow: rgba(140, 109, 88, 0.18);

            /* Typography */
            --text-heading: #2A211B;
            --text-body: #4D3E35;
            --text-muted: #827165;
        }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-cream-base);
            color: var(--text-body);
            min-height: 100vh;
            padding: 40px 60px;
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 36px;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-cream);
        }
        .title {
            font-family: 'Outfit', sans-serif;
            font-size: 28px;
            font-weight: 700;
            color: var(--text-heading);
        }
        .subtitle {
            font-size: 14px;
            color: var(--text-muted);
            margin-top: 4px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 36px;
        }
        .stat-card {
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 14px;
            padding: 22px 24px;
            box-shadow: 0 2px 10px rgba(122, 92, 71, 0.04);
        }
        .stat-label {
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            color: var(--text-muted);
            letter-spacing: 0.08em;
            margin-bottom: 8px;
        }
        .stat-value {
            font-family: 'Outfit', sans-serif;
            font-size: 34px;
            font-weight: 700;
            color: var(--text-heading);
        }
        .section-card {
            background: var(--bg-cream-card);
            border: 1px solid var(--border-cream);
            border-radius: 16px;
            padding: 28px;
            margin-bottom: 32px;
            box-shadow: 0 4px 18px rgba(122, 92, 71, 0.04);
        }
        .section-title {
            font-family: 'Outfit', sans-serif;
            font-size: 19px;
            font-weight: 700;
            color: var(--text-heading);
            margin-bottom: 20px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13.5px;
        }
        th {
            text-align: left;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-cream);
            color: var(--text-muted);
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        td {
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-cream);
            color: var(--text-body);
        }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: var(--brown-light); }
        .back-link {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: var(--brown-primary);
            text-decoration: none;
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 24px;
        }
    </style>
</head>
<body>
    <a href="/dashboard" class="back-link">← Return to Executive Dashboard</a>

    <div class="header">
        <div>
            <h1 class="title">Platform Governance & Multi-Tenant Registry</h1>
            <div class="subtitle">Platform Owner: Reshma Kodavali ({{ owner.email }})</div>
        </div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-muted);background:var(--surface-cream-tint);padding:8px 16px;border-radius:8px;border:1px solid var(--border-cream);">
            Active AI Engine: {{ llm_model }}
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Total Connected Users</div>
            <div class="stat-value" style="color:var(--brown-primary);">{{ total_users }}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Emails Processed</div>
            <div class="stat-value">{{ total_emails }}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Drafts Generated</div>
            <div class="stat-value" style="color:#4D7C0F;">{{ total_replies }}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Spam Blocked</div>
            <div class="stat-value" style="color:var(--brown-shy);">{{ total_spam }}</div>
        </div>
    </div>

    <div class="section-card">
        <h2 class="section-title">Registered Tenants</h2>
        <table>
            <thead>
                <tr>
                    <th>User</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Reply Mode</th>
                    <th>Connected At</th>
                    <th>Last Active</th>
                </tr>
            </thead>
            <tbody>
                {% for u in users %}
                <tr>
                    <td style="font-weight:600;color:var(--text-heading);">{{ u.name }}</td>
                    <td style="font-family:'JetBrains Mono',monospace;font-size:12px;">{{ u.email }}</td>
                    <td>
                        <span style="font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;background:var(--brown-light);color:var(--brown-primary);">
                            {{ u.role|upper }}
                        </span>
                    </td>
                    <td>{{ u.reply_mode }}</td>
                    <td style="font-size:12.5px;color:var(--text-muted);">{{ u.connected_at }}</td>
                    <td style="font-size:12.5px;color:var(--text-muted);">{{ u.last_active }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="section-card">
        <h2 class="section-title">Recent Cross-Tenant Triage Telemetry</h2>
        <table>
            <thead>
                <tr>
                    <th>Tenant</th>
                    <th>Subject</th>
                    <th>Sender</th>
                    <th>Classification</th>
                    <th>Priority</th>
                    <th>Action</th>
                    <th>Timestamp</th>
                </tr>
            </thead>
            <tbody>
                {% for a in activity %}
                <tr>
                    <td style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--brown-primary);">{{ a.user_email }}</td>
                    <td style="font-weight:600;max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ a.subject }}</td>
                    <td style="font-family:'JetBrains Mono',monospace;font-size:12px;">{{ a.sender }}</td>
                    <td>{{ a.classification }}</td>
                    <td>{{ a.priority }}</td>
                    <td>{{ a.status }}</td>
                    <td style="font-size:12.5px;color:var(--text-muted);">{{ a.received_at }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# Routes & Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if 'credentials' in session:
        return redirect(url_for('dashboard'))
    return render_template_string(HOME_PAGE)


@app.route("/login")
def login():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return """
        <div style="font-family:sans-serif;max-width:600px;margin:80px auto;padding:30px;background:#FAF8F3;border:1px solid #E6DDCE;border-radius:12px;text-align:center;">
            <h2 style="color:#C84B31;margin-bottom:12px;">credentials.json Missing</h2>
            <p style="color:#827165;line-height:1.6;">Please place your Google OAuth <code>credentials.json</code> file in the <code>files/</code> directory to authorize Gmail access.</p>
            <p><a href="/" style="color:#7A5C47;text-decoration:none;font-weight:600;">← Return to Home</a></p>
        </div>
        """, 400

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    connected_at = int(_time.time())
    session['connected_at'] = connected_at

    service = build('gmail', 'v1', credentials=credentials)
    prof = service.users().getProfile(userId='me').execute()
    user_email = prof.get('emailAddress', '').lower()

    user_name = user_email.split('@')[0].capitalize()
    user_pic  = ""
    try:
        oauth_service = build('oauth2', 'v2', credentials=credentials)
        user_info = oauth_service.userinfo().get().execute()
        user_name = user_info.get('name', user_name)
        user_pic  = user_info.get('picture', "")
    except Exception:
        pass

    role = "owner" if user_email == OWNER_EMAIL else "user"

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

    _ensure_background_thread()
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
        <div style="font-family:sans-serif;text-align:center;margin-top:120px;color:#C84B31;">
            <h1 style="font-size:32px;">403 Forbidden: Owner Access Required</h1>
            <p style="color:#827165;margin-top:8px;">Only the platform owner can access the Administrator Command Center.</p>
            <p><a href="/dashboard" style="color:#7A5C47;text-decoration:none;font-weight:600;">← Return to Dashboard</a></p>
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
