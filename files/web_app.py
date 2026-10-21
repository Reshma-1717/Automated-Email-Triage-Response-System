"""
web_app.py — Anyone can connect their Gmail and use your agent
"""

from flask import Flask, redirect, request, session, url_for, render_template_string
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os, json, base64, re, sqlite3, threading, time as _time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import openai as _openai

app = Flask(__name__)
app.secret_key = "change-this-to-random-string-123"
app.permanent_session_lifetime = timedelta(hours=24)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ── Owner / Platform Admin Configuration ────────────────────────────────────
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "reshmakodavali@gmail.com").strip().lower()

# ── Persistent SQLite DB (Multi-Tenant) ────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "aura_stats.db")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _db() as conn:
        # Multi-user accounts table
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
        # Multi-tenant processed emails table
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
        # Graceful migration for existing table
        cols = [r[1] for r in conn.execute("PRAGMA table_info(processed_emails)").fetchall()]
        if "user_email" not in cols:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN user_email TEXT DEFAULT ''")
        if "status" not in cols:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN status TEXT DEFAULT 'completed'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_user ON processed_emails (user_email, received_at)")
        conn.commit()

_init_db()

# ── LLM config (reads from environment, same as inference_gmail.py) ──
_LLM_API_KEY  = os.getenv("OPENAI_API_KEY", "")
_LLM_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
_LLM_MODEL    = os.getenv("MODEL_NAME", "gpt-4o-mini")

def _build_triage_prompt(user_name: str = "User", signature: str = None) -> str:
    """Dynamically personalizes prompt for any connected user, eliminating hardcoded names."""
    sig = signature if signature else user_name
    return f"""You are an intelligent executive AI email assistant for {user_name}. Analyse each email and respond with JSON only.

You must return exactly this JSON structure:
{{
  "classification": "spam" | "important" | "normal",
  "priority": "high" | "medium" | "low",
  "needs_reply": true | false,
  "reply": "<reply text or empty string>"
}}

Classification rules:
- "spam": phishing, scams, fake security alerts, unsolicited promotions, mass marketing, any email trying to trick or manipulate
- "important": a real human personally addressing {user_name}, needs an action or personal response
- "normal": newsletters, job board alerts, platform notifications, automated digests — even if from a known company

Priority rules:
- "high": deadline, interview, urgent personal request, offer, critical action
- "medium": needs attention within a few days
- "low": informational, newsletters, notifications

Reply rules — be strict:
- needs_reply: true ONLY when a real human wrote this personally to {user_name} and expects a personal response
- needs_reply: false for ALL of: spam, phishing, newsletters, job alerts, platform emails, no-reply senders, bulk mail, security notices, automated notifications
- If is_automated=true or is_bulk=true in the context, ALWAYS set needs_reply: false
- If needs_reply is false, reply must be empty string ""
- If needs_reply is true, write a short, professional, warm reply (under 80 words) as {user_name}, signed "{sig}"

Respond with valid JSON only. No markdown, no explanation."""

def _llm_triage(subject: str, sender: str, body: str, is_automated: bool, is_bulk: bool, user_name: str = "User", signature: str = None) -> dict:
    """Single LLM call that classifies, sets priority, decides reply, and writes it."""
    fallback = {
        "classification": "normal",
        "priority": "low" if (is_automated or is_bulk) else "medium",
        "needs_reply": False,
        "reply": ""
    }
    if not _LLM_API_KEY:
        # No API key: use safe fallback — never reply, basic classification
        return fallback
    try:
        client = _openai.OpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL)
        prompt = _build_triage_prompt(user_name=user_name, signature=signature)
        user_msg = (
            f"Context: is_automated={is_automated}, is_bulk={is_bulk}\n\n"
            f"Subject: {subject}\n"
            f"From: {sender}\n\n"
            f"Body:\n{body[:2000]}"
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
        # Safety guard: never reply if sender is automated/bulk regardless of LLM decision
        if is_automated or is_bulk:
            result["needs_reply"] = False
            result["reply"] = ""
        return result
    except Exception:
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

# ── Background agent state ──────────────────────────────────────────────────
_agent_state = {
    "running":       False,   # is the background thread active?
    "credentials":   None,    # Gmail OAuth credentials dict
    "connected_at":  0,       # Unix timestamp of first login
    "poll_interval": 60,      # seconds between polls
    "last_poll":     None,    # ISO timestamp of last poll
    "status":        "idle",  # idle | polling | error
}
_agent_lock = threading.Lock()


def _process_inbox(creds_dict: dict, connected_at: int, user_info: dict = None, reply_mode: str = "draft"):
    """Core processing logic — fetch new emails, triage, reply, save to DB."""
    from google.oauth2.credentials import Credentials as _Creds
    from googleapiclient.discovery import build as _build

    creds   = _Creds(**creds_dict)
    service = _build('gmail', 'v1', credentials=creds)

    user_info = user_info or {}
    user_email = user_info.get("email", "").lower()
    user_name  = user_info.get("name") or (user_email.split("@")[0].capitalize() if user_email else "User")
    signature  = user_info.get("signature") or user_name

    # Discover own address dynamically from profile if not supplied
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

    # Load already-processed IDs from DB (duplicate guard)
    with _db() as conn:
        seen = {r[0] for r in conn.execute("SELECT id FROM processed_emails")}

    results = service.users().messages().list(
        userId='me',
        q=f'label:INBOX -from:mailer-daemon -from:postmaster after:{connected_at}',
        maxResults=20
    ).execute()
    messages = results.get('messages', [])

    candidates = []
    for msg in messages:
        if msg['id'] in seen:
            continue
        msg_data = service.users().messages().get(
            userId='me', id=msg['id'], format='full'
        ).execute()
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

        body     = _extract_body(msg_data.get('payload', {}))[:2000]
        is_own   = any(e in _extract_email_address(sender) for e in OWN_EMAIL_ADDRESSES)
        is_auto  = (not is_own) and any(p in sdl for p in NO_REPLY_PATTERNS)
        is_bulk  = (not is_own) and any(p in sdl for p in BULK_SENDER_PATTERNS)
        sort_key = 1 if (is_auto or is_bulk) else 0
        candidates.append((sort_key, msg['id'], subject, sender, mid, thread_id, is_auto, is_bulk, body))

    candidates.sort(key=lambda x: x[0])

    for (_, msg_id, subject, sender, mid, thread_id, is_auto, is_bulk, body) in candidates:

        # ── Atomic claim ──────────────────────────────────────────────
        # Insert a placeholder row FIRST, before any slow work (LLM call,
        # sending the reply). If another process (background poll vs.
        # manual click) already claimed this email_id, the INSERT fails
        # with IntegrityError and we skip — preventing double replies.
        try:
            with _db() as conn:
                conn.execute("""
                    INSERT INTO processed_emails
                      (id, user_email, subject, sender, received_at, classification, priority, reply_sent, reply_text, status)
                    VALUES (?, ?, ?, ?, ?, 'pending', 'pending', 0, '', 'processing')
                """, (msg_id, user_email, subject[:120], sender[:120], datetime.now().isoformat(timespec='seconds')))
                conn.commit()
        except sqlite3.IntegrityError:
            # Another run already claimed this email — skip entirely
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
                else: # Default safe mode: save as draft in Gmail for review
                    service.users().drafts().create(
                        userId='me', body={'message': {'raw': raw, 'threadId': thread_id}}
                    ).execute()
                    reply_sent = True
                    status_action = "drafted"
            except Exception:
                reply_sent = False
                status_action = "error"

        # Finalize the claimed row with real results
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
    """Runs forever in a daemon thread — polls inbox every 60 s."""
    while True:
        with _agent_lock:
            creds        = _agent_state.get("credentials")
            connected_at = _agent_state.get("connected_at", 0)
            interval     = _agent_state.get("poll_interval", 60)

        if creds and connected_at:
            try:
                with _agent_lock:
                    _agent_state["status"]    = "polling"
                    _agent_state["last_poll"] = datetime.now().isoformat(timespec='seconds')
                _process_inbox(creds, connected_at)
                with _agent_lock:
                    _agent_state["status"] = "idle"
            except Exception as e:
                with _agent_lock:
                    _agent_state["status"] = f"error: {e}"

        _time.sleep(interval)


# Start the background thread once at startup — guarded against the
# Werkzeug debug reloader, which re-imports this module in a separate
# monitor process. Without this guard, two competing background loops
# would run simultaneously and fight over the same SQLite file.
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    _bg_thread = threading.Thread(target=_background_loop, daemon=True)
    _bg_thread.start()

HOME_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aura — AI Email Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

        :root {
            --gold: #C9A96E;
            --gold-light: #E8D5B0;
            --gold-dim: #8A6D42;
            --ink: #0A0A0A;
            --ink-2: #111111;
            --ink-3: #1A1A1A;
            --surface: #141414;
            --surface-2: #1E1E1E;
            --surface-3: #252525;
            --border: rgba(201,169,110,0.15);
            --border-strong: rgba(201,169,110,0.35);
            --text-primary: #F0EDE8;
            --text-secondary: #9A9189;
            --text-muted: #5C5650;
            --radius: 2px;
        }

        html { scroll-behavior: smooth; }

        body {
            font-family: 'DM Sans', sans-serif;
            background: var(--ink);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* ── Noise texture overlay ── */
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.03'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: 0;
            opacity: 0.4;
        }

        /* ── Ambient glow ── */
        .ambient {
            position: fixed;
            width: 600px;
            height: 600px;
            border-radius: 50%;
            filter: blur(120px);
            pointer-events: none;
            z-index: 0;
        }
        .ambient-1 {
            top: -200px; left: 50%;
            transform: translateX(-50%);
            background: radial-gradient(circle, rgba(201,169,110,0.07) 0%, transparent 70%);
        }
        .ambient-2 {
            bottom: -100px; right: -100px;
            width: 400px; height: 400px;
            background: radial-gradient(circle, rgba(201,169,110,0.04) 0%, transparent 70%);
        }

        /* ── Navigation ── */
        nav {
            position: fixed;
            top: 0; left: 0; right: 0;
            z-index: 100;
            padding: 24px 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(201,169,110,0.08);
            backdrop-filter: blur(20px);
            background: rgba(10,10,10,0.6);
        }

        .nav-logo {
            font-family: 'Cormorant Garamond', serif;
            font-size: 22px;
            font-weight: 500;
            letter-spacing: 0.08em;
            color: var(--text-primary);
            text-decoration: none;
        }
        .nav-logo span {
            color: var(--gold);
        }

        .nav-pill {
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: var(--text-muted);
            padding: 6px 14px;
            border: 1px solid var(--border);
            border-radius: 40px;
        }

        /* ── Hero ── */
        .hero {
            position: relative;
            z-index: 1;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 120px 40px 80px;
        }

        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.22em;
            text-transform: uppercase;
            color: var(--gold);
            margin-bottom: 40px;
            opacity: 0;
            animation: fadeUp 0.8s ease 0.2s forwards;
        }
        .eyebrow::before, .eyebrow::after {
            content: '';
            display: block;
            width: 24px;
            height: 1px;
            background: var(--gold-dim);
        }

        .hero-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: clamp(52px, 8vw, 96px);
            font-weight: 300;
            line-height: 1.05;
            letter-spacing: -0.01em;
            color: var(--text-primary);
            margin-bottom: 28px;
            opacity: 0;
            animation: fadeUp 0.9s ease 0.35s forwards;
        }
        .hero-title em {
            font-style: italic;
            color: var(--gold);
        }

        .hero-subtitle {
            font-size: 16px;
            font-weight: 300;
            line-height: 1.7;
            color: var(--text-secondary);
            max-width: 520px;
            margin-bottom: 60px;
            opacity: 0;
            animation: fadeUp 0.9s ease 0.5s forwards;
        }

        /* ── CTA Button ── */
        .cta-wrap {
            display: flex;
            align-items: center;
            gap: 20px;
            opacity: 0;
            animation: fadeUp 0.9s ease 0.65s forwards;
        }

        .btn-primary {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            background: var(--gold);
            color: var(--ink);
            font-family: 'DM Sans', sans-serif;
            font-size: 13px;
            font-weight: 500;
            letter-spacing: 0.06em;
            padding: 16px 36px;
            border-radius: var(--radius);
            text-decoration: none;
            transition: all 0.3s ease;
            border: none;
            cursor: pointer;
        }
        .btn-primary:hover {
            background: var(--gold-light);
            transform: translateY(-1px);
            box-shadow: 0 12px 40px rgba(201,169,110,0.25);
        }
        .btn-primary:active { transform: translateY(0); }

        .btn-secondary {
            font-size: 13px;
            font-weight: 400;
            color: var(--text-secondary);
            text-decoration: none;
            letter-spacing: 0.04em;
            transition: color 0.2s;
        }
        .btn-secondary:hover { color: var(--text-primary); }

        /* ── Divider ── */
        .divider-line {
            width: 1px;
            height: 40px;
            background: linear-gradient(to bottom, transparent, var(--border), transparent);
            margin: 80px auto 0;
            opacity: 0;
            animation: fadeUp 1s ease 0.9s forwards;
        }

        /* ── Features Section ── */
        .features {
            position: relative;
            z-index: 1;
            padding: 100px 60px;
            max-width: 1100px;
            margin: 0 auto;
        }

        .section-label {
            font-size: 10px;
            font-weight: 500;
            letter-spacing: 0.28em;
            text-transform: uppercase;
            color: var(--gold-dim);
            text-align: center;
            margin-bottom: 64px;
        }

        .features-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1px;
            background: var(--border);
            border: 1px solid var(--border);
        }

        .feature-card {
            background: var(--ink-2);
            padding: 48px 36px;
            transition: background 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        .feature-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 1px;
            background: linear-gradient(to right, transparent, var(--gold-dim), transparent);
            opacity: 0;
            transition: opacity 0.3s;
        }
        .feature-card:hover { background: var(--surface); }
        .feature-card:hover::before { opacity: 1; }

        .feature-icon {
            width: 36px;
            height: 36px;
            border: 1px solid var(--border);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 28px;
            font-size: 14px;
            background: var(--surface-2);
        }

        .feature-num {
            font-family: 'Cormorant Garamond', serif;
            font-size: 11px;
            letter-spacing: 0.14em;
            color: var(--gold-dim);
            margin-bottom: 14px;
        }

        .feature-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: 22px;
            font-weight: 500;
            color: var(--text-primary);
            margin-bottom: 12px;
            line-height: 1.2;
        }

        .feature-desc {
            font-size: 13px;
            font-weight: 300;
            line-height: 1.7;
            color: var(--text-muted);
        }

        /* ── Footer ── */
        footer {
            position: relative;
            z-index: 1;
            text-align: center;
            padding: 40px;
            border-top: 1px solid var(--border);
            font-size: 12px;
            color: var(--text-muted);
            letter-spacing: 0.05em;
        }

        /* ── Animations ── */
        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @media (max-width: 768px) {
            nav { padding: 20px 24px; }
            .features { padding: 60px 24px; }
            .features-grid { grid-template-columns: 1fr 1fr; }
            .hero-title { font-size: 48px; }
        }
        @media (max-width: 480px) {
            .features-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="ambient ambient-1"></div>
    <div class="ambient ambient-2"></div>

    <nav>
        <a href="/" class="nav-logo">AI Gmail Triage System (AI-GTS)</a>
        <span class="nav-pill">Stop worrying about your Email replys</span>
    </nav>

    <section class="hero">
        <div class="eyebrow">AI-Powered Email Assistant</div>
        <h1 class="hero-title">
            Your inbox,<br><em>reimagined</em>
        </h1>
        <p class="hero-subtitle">
            Connect Gmail. Let AI-GTS read, classify, prioritize, and reply to your emails — so you focus only on what matters.
        </p>
        <div class="cta-wrap">
            <a href="/connect" class="btn-primary">
                Connect Gmail &rarr;
            </a>
            <a href="#features" class="btn-secondary">See how it works</a>
        </div>
        <div class="divider-line"></div>
    </section>

    <section class="features" id="features">
        <p class="section-label">Capabilities</p>
        <div class="features-grid">
            <div class="feature-card">
                <div class="feature-icon">✦</div>
                <div class="feature-num">01</div>
                <h3 class="feature-title">Reads Emails</h3>
                <p class="feature-desc">Fetches your real inbox automatically and extracts context intelligently.</p>
            </div>
            <div class="feature-card">
                <div class="feature-icon">◈</div>
                <div class="feature-num">02</div>
                <h3 class="feature-title">AI Classifies</h3>
                <p class="feature-desc">Distinguishes spam, important, and normal emails with precision.</p>
            </div>
            <div class="feature-card">
                <div class="feature-icon">◎</div>
                <div class="feature-num">03</div>
                <h3 class="feature-title">Sets Priority</h3>
                <p class="feature-desc">Surfaces high-priority messages so nothing critical is missed.</p>
            </div>
            <div class="feature-card">
                <div class="feature-icon">⟡</div>
                <div class="feature-num">04</div>
                <h3 class="feature-title">Auto Replies</h3>
                <p class="feature-desc">Sends thoughtful replies only to real humans — never bots or spam.</p>
            </div>
        </div>
    </section>

    <footer>
        &copy; 2025 Aura &nbsp;&middot;&nbsp; AI Email Intelligence
    </footer>
</body>
</html>
"""

DASHBOARD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard — Aura</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Sans:wght@300;400;500&family=DM+Mono:wght@300;400&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

        :root {
            --gold: #C9A96E;
            --gold-light: #E8D5B0;
            --gold-dim: #8A6D42;
            --ink: #0A0A0A;
            --ink-2: #111111;
            --surface: #141414;
            --surface-2: #1E1E1E;
            --surface-3: #252525;
            --border: rgba(201,169,110,0.12);
            --border-strong: rgba(201,169,110,0.3);
            --text-primary: #F0EDE8;
            --text-secondary: #9A9189;
            --text-muted: #5C5650;
            --green: #4ADE80;
            --red: #F87171;
            --amber: #FBBF24;
        }

        body {
            font-family: 'DM Sans', sans-serif;
            background: var(--ink);
            color: var(--text-primary);
            min-height: 100vh;
        }

        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.03'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: 0;
            opacity: 0.4;
        }

        /* ── Layout ── */
        .layout {
            position: relative;
            z-index: 1;
            display: grid;
            grid-template-columns: 260px 1fr;
            min-height: 100vh;
        }

        /* ── Sidebar ── */
        .sidebar {
            border-right: 1px solid var(--border);
            padding: 40px 32px;
            display: flex;
            flex-direction: column;
            background: var(--ink-2);
        }

        .logo {
            font-family: 'Cormorant Garamond', serif;
            font-size: 24px;
            font-weight: 500;
            letter-spacing: 0.06em;
            margin-bottom: 48px;
            color: var(--text-primary);
        }
        .logo span { color: var(--gold); }

        .sidebar-label {
            font-size: 9px;
            font-weight: 500;
            letter-spacing: 0.24em;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 16px;
        }

        .sidebar-nav {
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-bottom: 40px;
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 14px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 400;
            color: var(--text-secondary);
            text-decoration: none;
            transition: all 0.2s;
            cursor: pointer;
            border: none;
            background: none;
            width: 100%;
            text-align: left;
        }
        .nav-item:hover { background: var(--surface-2); color: var(--text-primary); }
        .nav-item.active {
            background: rgba(201,169,110,0.1);
            color: var(--gold);
            border: 1px solid rgba(201,169,110,0.15);
        }
        .nav-item-icon {
            width: 16px;
            text-align: center;
            font-size: 12px;
            opacity: 0.7;
        }

        .sidebar-spacer { flex: 1; }

        .sidebar-user {
            padding: 16px;
            border: 1px solid var(--border);
            border-radius: 4px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .user-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: rgba(201,169,110,0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            color: var(--gold);
            font-weight: 500;
            flex-shrink: 0;
        }
        .user-info { flex: 1; min-width: 0; }
        .user-name {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .user-status {
            font-size: 11px;
            color: var(--green);
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .user-status::before {
            content: '';
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--green);
            display: inline-block;
        }

        .disconnect-btn {
            font-size: 11px;
            color: var(--text-muted);
            text-decoration: none;
            padding: 4px 8px;
            border-radius: 3px;
            transition: all 0.2s;
        }
        .disconnect-btn:hover { color: var(--red); background: rgba(248,113,113,0.08); }

        /* ── Main ── */
        .main {
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .topbar {
            padding: 28px 48px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .page-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: 28px;
            font-weight: 400;
            color: var(--text-primary);
            letter-spacing: -0.01em;
        }

        .run-btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: var(--gold);
            color: var(--ink);
            font-family: 'DM Sans', sans-serif;
            font-size: 13px;
            font-weight: 500;
            letter-spacing: 0.04em;
            padding: 12px 28px;
            border-radius: 2px;
            border: none;
            cursor: pointer;
            transition: all 0.25s ease;
        }
        .run-btn:hover {
            background: var(--gold-light);
            box-shadow: 0 8px 30px rgba(201,169,110,0.2);
            transform: translateY(-1px);
        }
        .run-btn:active { transform: translateY(0); }
        .run-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        /* ── Content ── */
        .content {
            flex: 1;
            padding: 40px 48px;
            overflow-y: auto;
        }

        /* ── Stats bar ── */
        .stats-bar {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1px;
            background: var(--border);
            border: 1px solid var(--border);
            margin-bottom: 40px;
        }

        .stat-cell {
            background: var(--ink-2);
            padding: 24px 28px;
        }

        .stat-label {
            font-size: 10px;
            font-weight: 500;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .stat-value {
            font-family: 'Cormorant Garamond', serif;
            font-size: 32px;
            font-weight: 400;
            color: var(--text-primary);
            line-height: 1;
        }
        .stat-value.gold { color: var(--gold); }
        .stat-sub {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* ── Empty / Output ── */
        .output-area {
            border: 1px solid var(--border);
        }

        .output-header {
            padding: 16px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 8px;
            background: var(--surface);
        }
        .output-header-title {
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--text-muted);
        }
        .output-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--text-muted);
        }
        .output-dot.live { background: var(--green); }

        .output-empty {
            padding: 80px 40px;
            text-align: center;
        }
        .output-empty-icon {
            font-size: 32px;
            margin-bottom: 20px;
            opacity: 0.3;
        }
        .output-empty-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: 22px;
            font-weight: 400;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }
        .output-empty-sub {
            font-size: 13px;
            color: var(--text-muted);
        }

        /* ── Email rows ── */
        .email-list { display: flex; flex-direction: column; }

        .email-row {
            display: grid;
            grid-template-columns: 1fr auto;
            align-items: start;
            padding: 20px 24px;
            border-bottom: 1px solid var(--border);
            gap: 20px;
            transition: background 0.15s;
        }
        .email-row:last-child { border-bottom: none; }
        .email-row:hover { background: rgba(255,255,255,0.02); }

        .email-index {
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            color: var(--text-muted);
            min-width: 20px;
        }
        .email-meta { flex: 1; min-width: 0; }
        .email-subject {
            font-size: 14px;
            font-weight: 500;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        }
        .email-sender {
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'DM Mono', monospace;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .email-left {
            display: flex;
            align-items: flex-start;
            gap: 16px;
        }

        .email-badges {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 6px;
        }

        .badge {
            font-size: 9px;
            font-weight: 500;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            padding: 3px 10px;
            border-radius: 1px;
        }

        .badge-spam { background: rgba(248,113,113,0.12); color: #F87171; border: 1px solid rgba(248,113,113,0.2); }
        .badge-important { background: rgba(251,191,36,0.12); color: #FBBF24; border: 1px solid rgba(251,191,36,0.2); }
        .badge-normal { background: rgba(74,222,128,0.08); color: #4ADE80; border: 1px solid rgba(74,222,128,0.15); }
        .badge-high { background: rgba(248,113,113,0.12); color: #F87171; border: 1px solid rgba(248,113,113,0.2); }
        .badge-medium { background: rgba(201,169,110,0.12); color: #C9A96E; border: 1px solid rgba(201,169,110,0.2); }
        .badge-low { background: rgba(255,255,255,0.04); color: var(--text-muted); border: 1px solid var(--border); }
        .badge-reply { background: rgba(74,222,128,0.08); color: #4ADE80; border: 1px solid rgba(74,222,128,0.15); }

        /* ── Processing state ── */
        .processing-state {
            padding: 60px 40px;
            text-align: center;
            display: none;
        }
        .processing-state.visible { display: block; }

        .pulse-ring {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            border: 1px solid var(--gold-dim);
            margin: 0 auto 20px;
            position: relative;
            animation: pulse 2s ease infinite;
        }
        .pulse-ring::after {
            content: '';
            position: absolute;
            inset: 8px;
            border-radius: 50%;
            background: rgba(201,169,110,0.2);
            animation: pulse 2s ease 0.5s infinite;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 0.7; }
            50% { transform: scale(1.1); opacity: 1; }
        }

        .processing-text {
            font-family: 'Cormorant Garamond', serif;
            font-size: 20px;
            font-weight: 400;
            color: var(--text-secondary);
        }
        .processing-sub {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 8px;
            font-family: 'DM Mono', monospace;
        }

        @media (max-width: 900px) {
            .layout { grid-template-columns: 1fr; }
            .sidebar { display: none; }
            .topbar, .content { padding: 24px; }
            .stats-bar { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <div class="layout">
        <!-- Sidebar -->
        <aside class="sidebar">
            <div class="logo">A<span>u</span>ra</div>

            <p class="sidebar-label">Navigation</p>
            <nav class="sidebar-nav">
                <button class="nav-item active">
                    <span class="nav-item-icon">◈</span>
                    Dashboard
                </button>
                <button class="nav-item" onclick="runAgent()">
                    <span class="nav-item-icon">▶</span>
                    Run Agent
                </button>
            </nav>

            <div class="sidebar-spacer"></div>

            {% if is_owner %}
            <div style="margin-bottom: 14px;">
                <a href="/admin" class="nav-item" style="color:var(--gold);background:rgba(201,169,110,0.08);border:1px solid rgba(201,169,110,0.3);text-decoration:none;display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:4px;font-size:12px;font-weight:500;">
                    <span>👑</span> Owner Command Center
                </a>
            </div>
            {% endif %}

            <div class="sidebar-user">
                {% if user.picture %}
                <img src="{{ user.picture }}" alt="{{ user.name }}" class="user-avatar" style="object-fit:cover;border:1px solid rgba(201,169,110,0.3);">
                {% else %}
                <div class="user-avatar">{{ (user.name or user.email or 'U')[0].upper() }}</div>
                {% endif %}
                <div class="user-info">
                    <div class="user-name" title="{{ user.email }}">{{ user.name or 'My Account' }}</div>
                    <div class="user-status" style="font-size:10.5px;">{{ '👑 Platform Owner' if is_owner else 'Connected' }}</div>
                </div>
                <a href="/disconnect" class="disconnect-btn" title="Disconnect">✕</a>
            </div>
        </aside>

        <!-- Main Content -->
        <div class="main">
            <div class="topbar">
                <h1 class="page-title">Inbox Intelligence</h1>
                <button class="run-btn" id="runBtn" onclick="runAgent()">
                    ▶&ensp;Run Agent
                </button>
            </div>

            <div class="content">
                <!-- Stats -->
                <div class="stats-bar" id="statsBar">
                    <div class="stat-cell">
                        <div class="stat-label">Total Received</div>
                        <div class="stat-value gold" id="statTotal">—</div>
                        <div class="stat-sub">emails</div>
                    </div>
                    <div class="stat-cell" id="statRepliesCell" style="cursor:pointer;" onclick="openRepliesModal()" title="Click to see sent replies">
                        <div class="stat-label">Replies Sent</div>
                        <div class="stat-value" id="statReplies">—</div>
                        <div class="stat-sub">click to view ↗</div>
                    </div>
                    <div class="stat-cell">
                        <div class="stat-label">Priority</div>
                        <div class="stat-value" id="statPriority">—</div>
                        <div class="stat-sub">high-priority</div>
                    </div>
                    <div class="stat-cell">
                        <div class="stat-label">Agent Status</div>
                        <div class="stat-value" id="statAgentStatus" style="font-size:18px;">—</div>
                        <div class="stat-sub" id="statLastPoll">running 24/7</div>
                    </div>
                </div>

                <!-- Output -->
                <div class="output-area">
                    <div class="output-header">
                        <div class="output-dot" id="outputDot"></div>
                        <span class="output-header-title" id="outputHeaderTitle">Awaiting Run</span>
                    </div>

                    <!-- Empty state -->
                    <div class="output-empty" id="emptyState">
                        <div class="output-empty-icon">✦</div>
                        <div class="output-empty-title">No emails processed yet</div>
                        <div class="output-empty-sub">Click "Run Agent" to analyze your inbox</div>
                    </div>

                    <!-- Processing state -->
                    <div class="processing-state" id="processingState">
                        <div class="pulse-ring"></div>
                        <div class="processing-text">Analyzing your inbox&hellip;</div>
                        <div class="processing-sub" id="processingStep">Connecting to Gmail</div>
                    </div>

                    <!-- Email list -->
                    <div class="email-list" id="emailList" style="display:none;"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Replies modal -->
    <div id="repliesModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.7); z-index:50; align-items:center; justify-content:center;" onclick="if(event.target===this)closeRepliesModal()">
        <div style="background:var(--surface); border:1px solid var(--border); width:min(720px,90vw); max-height:80vh; overflow-y:auto; border-radius:4px;">
            <div style="padding:20px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; background:var(--surface);">
                <h2 style="font-family:'Cormorant Garamond',serif; font-size:22px; font-weight:400; color:var(--text-primary);">Sent Replies</h2>
                <span style="cursor:pointer; color:var(--text-muted); font-size:20px;" onclick="closeRepliesModal()">✕</span>
            </div>
            <div id="repliesModalBody" style="padding:8px 0;"></div>
        </div>
    </div>

    <script>
        // ── Live stats: load on page load + auto-refresh every 10s ──
        async function loadStats() {
            try {
                const res = await fetch('/stats');
                const data = await res.json();
                document.getElementById('statTotal').textContent    = data.total_received ?? 0;
                document.getElementById('statReplies').textContent  = data.replies_sent ?? 0;
                document.getElementById('statPriority').textContent = data.high_priority ?? 0;

                const statusEl = document.getElementById('statAgentStatus');
                const pollEl   = document.getElementById('statLastPoll');
                const status   = data.agent_status || 'idle';
                statusEl.textContent = status === 'polling' ? 'Polling…' : (status.startsWith('error') ? 'Error' : 'Active');
                statusEl.style.color = status.startsWith('error') ? '#F87171' : (status === 'polling' ? 'var(--gold)' : 'var(--green)');
                pollEl.textContent = data.last_poll ? `last check: ${new Date(data.last_poll).toLocaleTimeString()}` : 'running 24/7';
            } catch (e) { /* silent — keep last known values */ }
        }
        loadStats();
        setInterval(loadStats, 10000);  // refresh every 10s — agent runs in background

        async function openRepliesModal() {
            const modal = document.getElementById('repliesModal');
            const body  = document.getElementById('repliesModalBody');
            modal.style.display = 'flex';
            body.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted);">Loading…</div>';
            try {
                const res = await fetch('/replies');
                const data = await res.json();
                if (!data.replies || data.replies.length === 0) {
                    body.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted);">No replies sent yet</div>';
                    return;
                }
                body.innerHTML = data.replies.map(r => `
                    <div style="padding:18px 24px; border-bottom:1px solid var(--border);">
                        <div style="display:flex; justify-content:space-between; gap:12px; margin-bottom:6px;">
                            <span style="font-weight:500; color:var(--text-primary); font-size:14px;">${escHtml(r.subject)}</span>
                            <span style="font-size:11px; color:var(--text-muted); white-space:nowrap;">${escHtml(r.received_at)}</span>
                        </div>
                        <div style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">${escHtml(r.sender)}</div>
                        <div style="background:var(--ink-2); border-left:2px solid var(--gold); padding:10px 14px; font-size:13px; color:var(--text-secondary); white-space:pre-wrap; line-height:1.5;">${escHtml(r.reply_text)}</div>
                    </div>
                `).join('');
            } catch (e) {
                body.innerHTML = '<div style="padding:40px; text-align:center; color:#F87171;">Failed to load replies</div>';
            }
        }

        function closeRepliesModal() {
            document.getElementById('repliesModal').style.display = 'none';
        }

        const processingSteps = [
            'Connecting to Gmail',
            'Fetching messages',
            'Running AI classification',
            'Evaluating priorities',
            'Composing replies',
            'Finalizing results'
        ];
        let stepIdx = 0, stepTimer = null;

        function startProcessingAnimation() {
            stepIdx = 0;
            document.getElementById('processingStep').textContent = processingSteps[0];
            stepTimer = setInterval(() => {
                stepIdx = (stepIdx + 1) % processingSteps.length;
                document.getElementById('processingStep').textContent = processingSteps[stepIdx];
            }, 1800);
        }

        function stopProcessingAnimation() {
            clearInterval(stepTimer);
        }

        async function runAgent() {
            const btn = document.getElementById('runBtn');
            btn.disabled = true;

            // Show processing
            document.getElementById('emptyState').style.display = 'none';
            document.getElementById('emailList').style.display = 'none';
            document.getElementById('processingState').classList.add('visible');
            document.getElementById('outputDot').classList.add('live');
            document.getElementById('outputHeaderTitle').textContent = 'Processing';
            startProcessingAnimation();

            try {
                const res = await fetch('/run');
                const data = await res.json();

                stopProcessingAnimation();
                document.getElementById('processingState').classList.remove('visible');

                if (data.error) {
                    document.getElementById('emptyState').style.display = 'block';
                    document.getElementById('emptyState').innerHTML = `
                        <div class="output-empty-icon" style="color:#F87171">✕</div>
                        <div class="output-empty-title">Something went wrong</div>
                        <div class="output-empty-sub" style="color:#F87171">${data.error}</div>
                    `;
                    document.getElementById('outputHeaderTitle').textContent = 'Error';
                    document.getElementById('outputDot').classList.remove('live');
                    document.getElementById('outputDot').style.background = '#F87171';
                    return;
                }

                // Refresh persistent stats from DB (cumulative totals, not just this run)
                loadStats();

                // Build email rows
                const list = document.getElementById('emailList');
                list.innerHTML = '';
                data.emails.forEach((e, i) => {
                    const row = document.createElement('div');
                    row.className = 'email-row';
                    row.innerHTML = `
                        <div class="email-left">
                            <span class="email-index">${String(i+1).padStart(2,'0')}</span>
                            <div class="email-meta">
                                <div class="email-subject">${escHtml(e.subject)}</div>
                                <div class="email-sender">${escHtml(e.sender)}</div>
                            </div>
                        </div>
                        <div class="email-badges">
                            <span class="badge badge-${e.classification}">${e.classification}</span>
                            <span class="badge badge-${e.priority}">${e.priority}</span>
                            ${e.reply_sent ? '<span class="badge badge-reply">replied</span>' : ''}
                        </div>
                    `;
                    list.appendChild(row);
                });

                list.style.display = 'flex';
                document.getElementById('outputHeaderTitle').textContent = `${data.total} Emails Processed`;
                document.getElementById('outputDot').classList.remove('live');
                document.getElementById('outputDot').style.background = '#4ADE80';

            } catch(err) {
                console.error('runAgent fetch failed:', err);
                stopProcessingAnimation();
                document.getElementById('processingState').classList.remove('visible');
                document.getElementById('emptyState').style.display = 'block';
                document.getElementById('emptyState').innerHTML = `
                    <div class="output-empty-icon" style="color:#F87171">✕</div>
                    <div class="output-empty-title">Connection failed</div>
                    <div class="output-empty-sub">${escHtml(err.message || 'Please try again')}</div>
                `;
            } finally {
                btn.disabled = false;
            }
        }

        function escHtml(str) {
            const d = document.createElement('div');
            d.textContent = str;
            return d.innerHTML;
        }
    </script>
</body>
</html>
"""


ADMIN_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Owner Command Center — Aura</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Sans:wght@300;400;500;700&family=DM+Mono:wght@300;400&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --gold: #C9A96E;
            --gold-light: #E8D5B0;
            --ink: #0A0A0A;
            --ink-2: #111111;
            --surface: #141414;
            --surface-2: #1E1E1E;
            --surface-3: #252525;
            --border: rgba(201,169,110,0.15);
            --text-primary: #F0EDE8;
            --text-secondary: #9A9189;
            --text-muted: #5C5650;
            --green: #4ADE80;
            --red: #F87171;
            --amber: #FBBF24;
        }
        body {
            font-family: 'DM Sans', sans-serif;
            background: var(--ink);
            color: var(--text-primary);
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
        .header-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: 32px;
            font-weight: 500;
            letter-spacing: 0.04em;
        }
        .header-title span { color: var(--gold); }
        .header-actions {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .btn {
            padding: 8px 16px;
            border-radius: 3px;
            font-size: 13px;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid var(--border);
            color: var(--text-primary);
            background: var(--surface);
        }
        .btn-gold {
            background: var(--gold);
            color: var(--ink);
            font-weight: 500;
            border-color: var(--gold);
        }
        .btn:hover { opacity: 0.9; }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 36px;
        }
        .stat-card {
            background: var(--surface);
            border: 1px solid var(--border);
            padding: 24px;
            border-radius: 4px;
        }
        .stat-label {
            font-size: 10px;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 10px;
        }
        .stat-val {
            font-family: 'Cormorant Garamond', serif;
            font-size: 38px;
            font-weight: 500;
            color: var(--gold);
        }
        .stat-sub {
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 4px;
        }

        /* Section Cards */
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 4px;
            margin-bottom: 32px;
            overflow: hidden;
        }
        .card-header {
            padding: 18px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .card-title {
            font-family: 'Cormorant Garamond', serif;
            font-size: 20px;
            font-weight: 500;
        }
        .badge {
            font-size: 9px;
            font-weight: 600;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            padding: 3px 8px;
            border-radius: 2px;
        }
        .badge-owner { background: rgba(201,169,110,0.15); color: var(--gold); border: 1px solid rgba(201,169,110,0.3); }
        .badge-user { background: rgba(255,255,255,0.06); color: var(--text-secondary); border: 1px solid var(--border); }
        .badge-green { background: rgba(74,222,128,0.12); color: var(--green); border: 1px solid rgba(74,222,128,0.2); }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
        }
        th {
            text-align: left;
            padding: 12px 20px;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
            background: var(--ink-2);
        }
        td {
            padding: 14px 20px;
            border-bottom: 1px solid rgba(201,169,110,0.08);
            color: var(--text-primary);
        }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: rgba(255,255,255,0.015); }
        .user-cell {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .avatar-sm {
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: rgba(201,169,110,0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            color: var(--gold);
            font-weight: 600;
        }
        .mono { font-family: 'DM Mono', monospace; font-size: 11px; color: var(--text-secondary); }
    </style>
</head>
<body>
    <header class="header">
        <div>
            <div class="header-title">A<span>u</span>ra — Owner Command Center</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                Administrator Portal  •  Connected as Owner: <span style="color:var(--gold);">{{ owner.email }}</span>
            </div>
        </div>
        <div class="header-actions">
            <a href="/dashboard" class="btn btn-gold">← Return to My Dashboard</a>
            <a href="/disconnect" class="btn">Log Out</a>
        </div>
    </header>

    <!-- Global Metrics Grid -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Total Registered Users</div>
            <div class="stat-val">{{ total_users }}</div>
            <div class="stat-sub">Accounts connected</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Platform Emails Triaged</div>
            <div class="stat-val">{{ total_emails }}</div>
            <div class="stat-sub">Across all user inboxes</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">AI Replies Generated</div>
            <div class="stat-val">{{ total_replies }}</div>
            <div class="stat-sub">Automated drafts & sends</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Spam Blocked</div>
            <div class="stat-val">{{ total_spam }}</div>
            <div class="stat-sub">Malicious & phishing emails</div>
        </div>
    </div>

    <!-- Registered Users Table -->
    <div class="card">
        <div class="card-header">
            <div class="card-title">Registered Users Directory</div>
            <span class="badge badge-green">{{ users|length }} Active Accounts</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>User</th>
                    <th>Email Address</th>
                    <th>Role</th>
                    <th>Reply Safety Mode</th>
                    <th>Joined</th>
                    <th>Last Active</th>
                </tr>
            </thead>
            <tbody>
                {% for u in users %}
                <tr>
                    <td>
                        <div class="user-cell">
                            {% if u.picture %}
                            <img src="{{ u.picture }}" style="width:26px;height:26px;border-radius:50%;object-fit:cover;">
                            {% else %}
                            <div class="avatar-sm">{{ (u.name or u.email or 'U')[0].upper() }}</div>
                            {% endif %}
                            <span style="font-weight:500;">{{ u.name or 'Anonymous User' }}</span>
                        </div>
                    </td>
                    <td class="mono">{{ u.email }}</td>
                    <td>
                        {% if u.role == 'owner' or u.email == owner.email %}
                        <span class="badge badge-owner">👑 Owner</span>
                        {% else %}
                        <span class="badge badge-user">User</span>
                        {% endif %}
                    </td>
                    <td><span class="mono">{{ u.reply_mode or 'draft' }}</span></td>
                    <td class="mono">{{ (u.connected_at or '—')[:16] }}</td>
                    <td class="mono">{{ (u.last_active or '—')[:16] }}</td>
                </tr>
                {% else %}
                <tr>
                    <td colspan="6" style="text-align:center;padding:30px;color:var(--text-muted);">No users registered yet.</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Live Platform Activity Stream -->
    <div class="card">
        <div class="card-header">
            <div class="card-title">Live Platform Activity Stream</div>
            <span style="font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;">Last 15 triage actions platform-wide</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>User</th>
                    <th>Sender</th>
                    <th>Subject</th>
                    <th>Classification</th>
                    <th>Priority</th>
                    <th>Reply Action</th>
                    <th>Time</th>
                </tr>
            </thead>
            <tbody>
                {% for act in activity %}
                <tr>
                    <td class="mono" style="color:var(--gold);">{{ act.user_email }}</td>
                    <td class="mono">{{ (act.sender or '')[:28] }}</td>
                    <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ act.subject }}</td>
                    <td><span class="badge" style="background:rgba(255,255,255,0.06);color:var(--text-secondary);">{{ act.classification }}</span></td>
                    <td><span class="badge" style="background:rgba(255,255,255,0.06);color:var(--text-secondary);">{{ act.priority }}</span></td>
                    <td>
                        {% if act.reply_sent %}
                        <span class="badge badge-green">✓ {{ act.status or 'Replied' }}</span>
                        {% else %}
                        <span style="color:var(--text-muted);font-size:11px;">No reply</span>
                        {% endif %}
                    </td>
                    <td class="mono">{{ (act.received_at or '')[:16] }}</td>
                </tr>
                {% else %}
                <tr>
                    <td colspan="7" style="text-align:center;padding:30px;color:var(--text-muted);">No emails triaged yet.</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- System Health -->
    <div class="card" style="padding:20px 24px;display:flex;align-items:center;justify-content:space-between;">
        <div>
            <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-muted);margin-bottom:4px;">Core Intelligence Engine</div>
            <div style="font-size:13px;color:var(--gold);">LLM Model: {{ llm_model }}  •  Status: Operational (Ready for Public Traffic)</div>
        </div>
        <span class="badge badge-green">● All Systems Online</span>
    </div>
</body>
</html>
"""

# ── Routes ─────────────────────────────────────────────────

@app.route("/")
def index():
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
    # Save the code_verifier so the callback can complete the PKCE exchange
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
    # Restore the PKCE code_verifier from the session
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
    import time as _t
    connected_at = int(_t.time())
    session['connected_at'] = connected_at
    session['seen_ids'] = []

    # ── Fetch user profile from Gmail & OAuth userinfo ──
    user_email = ""
    user_name  = ""
    user_pic   = ""
    try:
        gmail_service = build('gmail', 'v1', credentials=creds)
        prof = gmail_service.users().getProfile(userId='me').execute()
        user_email = prof.get('emailAddress', '').lower()
    except Exception as e:
        print(f"Notice: Gmail profile fetch: {e}")

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

    # Register/update user record in database
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

    # ── Activate background agent ──
    with _agent_lock:
        _agent_state["credentials"]  = dict(session['credentials'])
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

    # Re-activate background agent if Flask restarted
    with _agent_lock:
        if not _agent_state.get("credentials"):
            _agent_state["credentials"]  = dict(session['credentials'])
            _agent_state["connected_at"] = session.get('connected_at', 0)
            _agent_state["running"]      = True

    is_owner = (user.get('role') == 'owner' or user.get('email', '').lower() == OWNER_EMAIL)
    return render_template_string(DASHBOARD_PAGE, user=user, is_owner=is_owner)


@app.route("/admin")
def admin_portal():
    """Owner & Platform Administrator Control Center."""
    user = session.get('user')
    if not user:
        return redirect(url_for('index'))
    if user.get('role') != 'owner' and user.get('email', '').lower() != OWNER_EMAIL:
        return """
        <div style="font-family:sans-serif;text-align:center;margin-top:120px;color:#F87171;">
            <h1 style="font-size:32px;">403 Forbidden: Owner Access Required</h1>
            <p style="color:#9A9189;margin-top:8px;">Only the platform owner can access the Administrator Command Center.</p>
            <p><a href="/dashboard" style="color:#C9A96E;text-decoration:none;font-weight:600;">← Return to My Dashboard</a></p>
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


@app.route("/api/settings", methods=["GET", "POST"])
def user_settings():
    """View or update user reply mode (draft vs send) and signature."""
    user = session.get('user')
    if not user or not user.get('email'):
        return {"error": "Not authenticated"}, 401

    user_email = user['email']
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        reply_mode = data.get("reply_mode", user.get("reply_mode", "draft"))
        signature  = data.get("signature", user.get("signature", ""))
        
        session['user']['reply_mode'] = reply_mode
        session['user']['signature']  = signature

        with _db() as conn:
            conn.execute("""
                UPDATE users SET reply_mode = ?, signature = ?, last_active = datetime('now')
                WHERE email = ?
            """, (reply_mode, signature, user_email))
            conn.commit()

        return {"status": "success", "reply_mode": reply_mode, "signature": signature}

    return {
        "email": user_email,
        "name": user.get("name", ""),
        "reply_mode": user.get("reply_mode", "draft"),
        "signature": user.get("signature", "")
    }


@app.route("/run")
def run_agent():
    """Manual trigger — isolated per authenticated user."""
    if 'credentials' not in session:
        return {"error": "Not connected"}, 401

    try:
        creds_dict   = dict(session['credentials'])
        connected_at = session.get('connected_at', 0)
        user         = session.get('user', {})
        user_email   = user.get('email', '').lower()
        reply_mode   = user.get('reply_mode', 'draft')

        with _db() as conn:
            if user_email:
                before_count = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]
            else:
                before_count = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]

        _process_inbox(creds_dict, connected_at, user_info=user, reply_mode=reply_mode)

        with _db() as conn:
            if user_email:
                after_count = conn.execute("SELECT COUNT(*) FROM processed_emails WHERE user_email = ?", (user_email,)).fetchone()[0]
                rows = conn.execute("""
                    SELECT subject, sender, classification, priority, reply_sent, status
                    FROM processed_emails
                    WHERE user_email = ?
                    ORDER BY rowid DESC
                    LIMIT ?
                """, (user_email, max(1, after_count - before_count))).fetchall()
            else:
                after_count = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
                rows = conn.execute("""
                    SELECT subject, sender, classification, priority, reply_sent, status
                    FROM processed_emails
                    ORDER BY rowid DESC
                    LIMIT ?
                """, (max(1, after_count - before_count),)).fetchall()

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
        _agent_state["connected_at"] = 0
        _agent_state["running"]      = False
        _agent_state["status"]       = "idle"
    session.clear()
    return redirect(url_for('index'))


if __name__ == "__main__":
    app.run(debug=True, port=5000)