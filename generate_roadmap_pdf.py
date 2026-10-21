"""
Script to generate a publication-quality PDF report:
'AURA AI Email Intelligence: Production Readiness & Architecture Roadmap'
"""

import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(36, letter[1] - 25, "AURA AI EMAIL INTELLIGENCE  |  PRODUCTION ARCHITECTURE ROADMAP")
            self.drawRightString(letter[0] - 36, letter[1] - 25, "CONFIDENTIAL & PROPRIETARY")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(36, letter[1] - 29, letter[0] - 36, letter[1] - 29)

        # Footer (all pages)
        self.setFont("Helvetica", 8)
        self.drawString(36, 20, "AI Email Triage System - SaaS Edition  |  Engineering Review & Launch Roadmap")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 36, 20, page_str)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(36, 28, letter[0] - 36, 28)
        self.restoreState()


def build_pdf(filename="AURA_Production_Website_Roadmap.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    c_primary = colors.HexColor("#0F172A")    # Deep Navy
    c_secondary = colors.HexColor("#4F46E5")  # Indigo
    c_dark = colors.HexColor("#1E293B")       # Dark Slate
    c_muted = colors.HexColor("#64748B")      # Muted Slate
    c_danger = colors.HexColor("#DC2626")     # Red
    c_warning = colors.HexColor("#D97706")    # Amber
    c_success = colors.HexColor("#059669")    # Emerald

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=21,
        leading=25,
        textColor=c_primary,
        spaceAfter=3
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=c_secondary,
        spaceAfter=6
    )

    meta_style = ParagraphStyle(
        'DocMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=c_muted
    )

    h1_style = ParagraphStyle(
        'SectionH1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12.5,
        leading=16,
        textColor=c_primary,
        spaceBefore=8,
        spaceAfter=5,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'SectionH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13,
        textColor=c_secondary,
        spaceBefore=6,
        spaceAfter=2,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        textColor=c_dark,
        spaceAfter=4
    )

    bullet_style = ParagraphStyle(
        'BulletText',
        parent=body_style,
        leftIndent=10,
        firstLineIndent=-7,
        spaceAfter=2
    )

    table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white
    )

    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=c_dark
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=10,
        textColor=c_dark
    )

    badge_danger = ParagraphStyle(
        'BadgeDanger',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=c_danger
    )

    badge_success = ParagraphStyle(
        'BadgeSuccess',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=c_success
    )

    badge_warning = ParagraphStyle(
        'BadgeWarning',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=c_warning
    )

    story = []

    # =========================================================================
    # PAGE 1: TITLE, EXECUTIVE SUMMARY & ENDPOINT TEST MATRIX
    # =========================================================================
    story.append(Paragraph("AURA AI EMAIL INTELLIGENCE", title_style))
    story.append(Paragraph("Production Readiness Test Audit & Phased Architecture Roadmap", subtitle_style))
    
    meta_text = "<b>Document Type:</b> Production Engineering Assessment  |  <b>Target Release:</b> Commercial Public SaaS  |  <b>Current Version:</b> 1.0 Prototype"
    story.append(Paragraph(meta_text, meta_style))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_secondary, spaceAfter=8))

    story.append(Paragraph("1. Executive Summary & Test Audit Results", h1_style))
    exec_summary = (
        "An automated test audit of <b>web_app.py</b> and its underlying services was executed against active endpoints. "
        "The core LLM triage pipeline, atomic SQLite placeholder claims, and OAuth URL generation function successfully in a local single-user environment. "
        "However, <b>the application is currently architected as a single-user local prototype</b>. "
        "To publish this as a live, secure, commercial multi-tenant website, critical architectural changes are required to address "
        "session token leakage, lack of tenant database isolation, hardcoded user personality prompts, and Google Restricted Scope compliance."
    )
    story.append(Paragraph(exec_summary, body_style))
    story.append(Spacer(1, 4))

    # Readiness Score Box
    score_data = [
        [
            Paragraph("<b>Current Prototype Readiness Score:</b> 3.5 / 10", ParagraphStyle('Score', fontName='Helvetica-Bold', fontSize=9, textColor=c_danger)),
            Paragraph("<b>Target Go-Live Score:</b> 9.8 / 10", ParagraphStyle('ScoreT', fontName='Helvetica-Bold', fontSize=9, textColor=c_success)),
            Paragraph("<b>Primary Blocker:</b> Google Restricted Scope Audit (CASA Tier 2)", ParagraphStyle('ScoreB', fontName='Helvetica', fontSize=8, textColor=c_dark))
        ]
    ]
    t_score = Table(score_data, colWidths=[170, 150, 220])
    t_score.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_score)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Automated Endpoint Test Matrix", h2_style))

    audit_data = [
        [
            Paragraph("Endpoint", table_header),
            Paragraph("Method", table_header),
            Paragraph("Test Result", table_header),
            Paragraph("Audit Finding & Defect Description", table_header),
            Paragraph("Risk", table_header)
        ],
        [
            Paragraph("<b>GET /</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("PASS (200 OK)", badge_success),
            Paragraph("Landing page renders properly with CSS and typography. Missing CSRF meta tag and Content Security Policy (CSP).", table_cell),
            Paragraph("LOW", badge_warning)
        ],
        [
            Paragraph("<b>GET /connect</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("PASS (302 Redirect)", badge_success),
            Paragraph("Generates Google OAuth URL with PKCE. Relies on local <i>credentials.json</i>; fails if file missing in cloud container.", table_cell),
            Paragraph("MED", badge_warning)
        ],
        [
            Paragraph("<b>GET /callback</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("CRITICAL RISK", badge_danger),
            Paragraph("Stores entire OAuth credentials dict (client_secret, refresh_token) in client-side signed cookie. High risk of token compromise.", table_cell),
            Paragraph("CRIT", badge_danger)
        ],
        [
            Paragraph("<b>GET /dashboard</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("PASS (302/200)", badge_success),
            Paragraph("Unauthenticated redirect works. Reactivates in-memory thread; will conflict in multi-worker production WSGI servers.", table_cell),
            Paragraph("HIGH", badge_danger)
        ],
        [
            Paragraph("<b>GET /run</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("PASS (401/200)", badge_success),
            Paragraph("Returns 401 when unauthorized. When authorized, runs synchronous blocking LLM calls that risk Nginx gateway 504 timeouts.", table_cell),
            Paragraph("HIGH", badge_danger)
        ],
        [
            Paragraph("<b>GET /stats</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("DATA LEAK", badge_danger),
            Paragraph("Queries <i>processed_emails</i> without user_id filter. In multi-tenant deployment, User B sees User A's email statistics.", table_cell),
            Paragraph("CRIT", badge_danger)
        ],
        [
            Paragraph("<b>GET /replies</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("DATA LEAK", badge_danger),
            Paragraph("Returns all sent reply texts across all users from SQLite without account separation. Severe privacy breach risk.", table_cell),
            Paragraph("CRIT", badge_danger)
        ],
        [
            Paragraph("<b>GET /disconnect</b>", table_cell),
            Paragraph("GET", table_cell),
            Paragraph("PASS (302)", badge_success),
            Paragraph("Clears local session cookie. Does not revoke OAuth token on Google OAuth servers.", table_cell),
            Paragraph("LOW", badge_warning)
        ],
    ]

    t_audit = Table(audit_data, colWidths=[65, 36, 74, 325, 40])
    t_audit.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_primary),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    story.append(t_audit)

    story.append(PageBreak())

    # =========================================================================
    # PAGE 2: TOP 6 BLOCKERS & ARCHITECTURAL VULNERABILITIES
    # =========================================================================
    story.append(Paragraph("2. Critical Architectural Blockers & Security Vulnerabilities", h1_style))
    story.append(Paragraph(
        "Before publishing this web application to the public internet, the following six core flaws must be resolved to protect user data, ensure uptime, and avoid account suspension by Google.",
        body_style
    ))
    story.append(Spacer(1, 4))

    blockers = [
        ("Blocker 1: Google Restricted Scopes Verification (CASA Assessment)",
         "The app requests <i>https://www.googleapis.com/auth/gmail.send</i> and <i>gmail.readonly</i>. Because these access private user emails, Google immediately flags unverified apps with a severe warning ('Google hasn't verified this app') and caps access at 100 manual test accounts. Publishing to real users requires passing a third-party Cloud Application Security Assessment (CASA Tier 2 audit), hosting public legal policies, and completing official Google OAuth Verification.",
         c_danger),
        ("Blocker 2: Plaintext Hardcoded Secrets & Client-Side Token Storage",
         "The Flask secret key is hardcoded to a default string ('change-this-to-random-string-123'), and <i>OAUTHLIB_INSECURE_TRANSPORT=1</i> permits plaintext HTTP transmission. Most critically, the full Google OAuth token dict (including refresh_token and client_secret) is serialized into the browser's session cookie. Storing long-lived refresh tokens in browser cookies violates OAuth security standards; tokens must be encrypted at rest in a server-side vault.",
         c_danger),
        ("Blocker 3: Hardcoded Single-User Personality & Logic",
         "The system prompt is explicitly hardcoded to a single individual: <i>'You are an AI email assistant for Reshma Kodavali... signed Reshma'</i>. In addition, internal filters only recognise <i>reshmakodavali@gmail.com</i>. Connecting any external user causes the agent to impersonate Reshma and fail to detect the new user's own emails. The prompt and sender detection must be made fully dynamic.",
         c_danger),
        ("Blocker 4: Lack of Multi-Tenant Database Isolation (SQLite)",
         "The SQLite database (<i>aura_stats.db</i>) contains a single <i>processed_emails</i> table with no <i>user_id</i> or tenant partitioning. If multiple users connect, User B's dashboard displays User A's email subjects, statistics, and replies. Furthermore, SQLite experiences database locking errors when multiple Gunicorn/Uvicorn workers write concurrently.",
         c_danger),
        ("Blocker 5: In-Memory Background Threading vs. Distributed Queues",
         "The agent runs as a daemon thread (<i>threading.Thread</i>) polling the inbox every 60 seconds. In modern containerized production hosting (AWS ECS, Google Cloud Run, Render), worker instances restart or autoscale, causing the thread to terminate or multiple instances to spawn duplicate polling jobs that double-reply to emails. A distributed Redis + Celery worker queue is required.",
         c_warning),
        ("Blocker 6: Absence of Safety Net, Draft Review & Quota Controls",
         "The current prototype immediately sends AI-generated replies directly to external senders. Outbound auto-replies to mailing lists, customer support tickets, or vacation auto-responders risk creating infinite email loops. A mandatory 'Draft First' mode and daily OpenAI token cost caps must be enforced.",
         c_warning)
    ]

    for title, desc, col in blockers:
        story.append(Paragraph(f"<b>{title}</b>", ParagraphStyle('BTitle', fontName='Helvetica-Bold', fontSize=9, textColor=col, spaceBefore=4, spaceAfter=2, keepWithNext=True)))
        story.append(Paragraph(desc, body_style))

    story.append(PageBreak())

    # =========================================================================
    # PAGE 3: PHASED ROADMAP (PHASES 1 TO 4)
    # =========================================================================
    story.append(Paragraph("3. Production Implementation Roadmap (Phases 1 - 4)", h1_style))
    story.append(Paragraph("Step-by-step technical plan to transform the local prototype into a secure, commercial-grade platform.", body_style))
    story.append(Spacer(1, 4))

    # Phase 1
    story.append(Paragraph("PHASE 1: Multi-Tenancy & Database Modernization (Weeks 1 - 2)", h2_style))
    p1_items = [
        "<b>PostgreSQL Migration:</b> Replace SQLite with managed PostgreSQL (Supabase, Neon, or AWS RDS) with PgBouncer connection pooling.",
        "<b>Relational Multi-Tenant Schema:</b>",
        "&nbsp;&nbsp;&bull; <code>users</code>: uuid, email, full_name, plan_tier, created_at, is_active.",
        "&nbsp;&nbsp;&bull; <code>oauth_credentials</code>: user_id, encrypted_token, encrypted_refresh_token, expiry, scopes.",
        "&nbsp;&nbsp;&bull; <code>email_logs</code>: id, user_id, message_id, thread_id, sender, subject, classification, priority, reply_status, reply_text.",
        "&nbsp;&nbsp;&bull; <code>user_settings</code>: user_id, custom_signature, tone_mode (Formal/Warm/Concise), whitelist_senders, auto_send_enabled.",
        "<b>Dynamic Prompting:</b> Replace hardcoded Reshma prompts with parameterized templates: <i>'You are an executive assistant for {user.full_name}. Write replies in {user.tone} style and sign as {user.signature}.'</i>",
        "<b>Tenant Query Isolation:</b> Enforce <code>WHERE user_id = current_user.id</code> on all statistics, email views, and reply queries."
    ]
    for it in p1_items:
        story.append(Paragraph(it, bullet_style))

    # Phase 2
    story.append(Paragraph("PHASE 2: Security Hardening & Zero-Trust Token Vault (Week 3)", h2_style))
    p2_items = [
        "<b>Token Encryption at Rest:</b> Encrypt OAuth refresh tokens before saving to database using AES-256 GCM (Fernet or AWS KMS). Never expose tokens in cookies.",
        "<b>Server-Side Redis Sessions:</b> Replace signed client cookies with server-side Redis sessions (Flask-Session). Set <code>Secure=True</code>, <code>HttpOnly=True</code>, <code>SameSite=Lax</code>.",
        "<b>Secret Management:</b> Remove <code>OAUTHLIB_INSECURE_TRANSPORT</code>. Load client secrets and API keys from Google Secret Manager / AWS SSM.",
        "<b>CSRF & Security Headers:</b> Implement Flask-WTF CSRFProtect across all routes. Add Content Security Policy (CSP), X-Frame-Options: DENY, and HSTS headers.",
        "<b>API Rate Limiting:</b> Deploy Flask-Limiter backed by Redis to prevent automated endpoint flooding (e.g. max 10 manual triage runs/hr)."
    ]
    for it in p2_items:
        story.append(Paragraph(it, bullet_style))

    # Phase 3
    story.append(Paragraph("PHASE 3: Asynchronous Scalability & Gmail Webhooks (Week 4)", h2_style))
    p3_items = [
        "<b>Celery + Redis Worker Architecture:</b> Offload heavy LLM triage calls to Celery background workers; keep Flask web requests fast and non-blocking.",
        "<b>Google Cloud Pub/Sub Webhooks:</b> Register Gmail Push Notifications (<code>users.watch</code>) to receive instant email notifications instead of polling every 60s.",
        "<b>Distributed Idempotency (Redlock):</b> Use Redis distributed locks keyed on <code>(user_id, message_id)</code> to ensure an email is never replied to twice under concurrent execution.",
        "<b>Resilient LLM Execution:</b> Implement retry exponential backoff with Tenacity, token budgeting, and fallback models (gpt-4o-mini with Claude 3.5 Haiku fallback)."
    ]
    for it in p3_items:
        story.append(Paragraph(it, bullet_style))

    # Phase 4
    story.append(Paragraph("PHASE 4: Google OAuth Verification & Legal Compliance (Weeks 5 - 6)", h2_style))
    p4_items = [
        "<b>Custom Domain & SSL:</b> Provision branded domain (e.g., <code>https://auraemail.com</code>) with Cloudflare / AWS Route53 SSL.",
        "<b>Hosted Legal Compliance Pages:</b> Create dedicated <code>/privacy-policy</code> and <code>/terms-of-service</code> disclosing compliance with the <i>Google API Services User Data Policy</i> (Limited Use disclosure).",
        "<b>Search Console Verification:</b> Complete DNS TXT record ownership verification in Google Search Console.",
        "<b>CASA Tier 2 Security Assessment:</b> Complete the third-party CASA security scan and submit proof of compliance to Google Trust & Safety.",
        "<b>OAuth Consent Demo Video:</b> Record and submit the required YouTube video showing the complete consent flow and demonstrating scope usage."
    ]
    for it in p4_items:
        story.append(Paragraph(it, bullet_style))

    story.append(PageBreak())

    # =========================================================================
    # PAGE 4: ROADMAP (PHASES 5-6), TECH MATRIX & LAUNCH CHECKLIST
    # =========================================================================
    story.append(Paragraph("4. Production Implementation Roadmap (Phases 5 - 6)", h1_style))

    # Phase 5
    story.append(Paragraph("PHASE 5: Commercial SaaS Features, UX & Monetization (Weeks 7 - 8)", h2_style))
    p5_items = [
        "<b>'Draft First' Safe Mode:</b> Default all accounts to creating Gmail Drafts instead of direct sending, allowing users to review AI replies before sending.",
        "<b>Tone & Persona Customizer:</b> Allow users to select their communication tone (Executive Concise, Warm Professional, Casual Founder).",
        "<b>Real-Time UI Updates:</b> Replace static dashboard polling with Server-Sent Events (SSE) or WebSockets to show live triage activity feeds.",
        "<b>Stripe Billing Integration:</b> Implement subscription plans (Free: 50 emails/mo, Pro: 1,000 emails/mo, Enterprise: Unlimited). Enforce monthly usage quotas."
    ]
    for it in p5_items:
        story.append(Paragraph(it, bullet_style))

    # Phase 6
    story.append(Paragraph("PHASE 6: Cloud Deployment, CI/CD & Observability (Week 9)", h2_style))
    p6_items = [
        "<b>Production WSGI Containerization:</b> Multi-stage Dockerfile packaging Gunicorn WSGI server with Uvicorn workers behind Nginx reverse proxy.",
        "<b>Serverless Cloud Hosting:</b> Deploy to Google Cloud Run or AWS ECS Fargate with automated auto-scaling (0 to N instances).",
        "<b>Automated CI/CD Pipeline:</b> GitHub Actions workflow executing pytest suites, security linting (Bandit, Flake8), and automatic staging deployments.",
        "<b>Full-Stack Observability:</b> Sentry integration for real-time error tracking; Prometheus & Grafana for latency and LLM token cost telemetry."
    ]
    for it in p6_items:
        story.append(Paragraph(it, bullet_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("5. Architecture Comparison: Prototype vs. Production", h1_style))
    
    matrix_data = [
        [
            Paragraph("System Dimension", table_header),
            Paragraph("Current Prototype (web_app.py)", table_header),
            Paragraph("Target Production Architecture", table_header)
        ],
        [
            Paragraph("<b>Database</b>", table_cell_bold),
            Paragraph("Single SQLite file (<i>aura_stats.db</i>), no user isolation", table_cell),
            Paragraph("Managed PostgreSQL (Supabase/RDS) + row-level security", table_cell)
        ],
        [
            Paragraph("<b>OAuth Token Vault</b>", table_cell_bold),
            Paragraph("Plaintext in client-side session cookie", table_cell),
            Paragraph("AES-256 encrypted at rest in DB vault + server-side Redis sessions", table_cell)
        ],
        [
            Paragraph("<b>Background Jobs</b>", table_cell_bold),
            Paragraph("In-memory Python thread (<i>threading.Thread</i>) polling 60s", table_cell),
            Paragraph("Celery worker pool + Redis queue + Google Cloud Pub/Sub Webhooks", table_cell)
        ],
        [
            Paragraph("<b>User Personalization</b>", table_cell_bold),
            Paragraph("Hardcoded to Reshma Kodavali in system prompt", table_cell),
            Paragraph("Dynamic user profile injection (name, tone, signature, custom rules)", table_cell)
        ],
        [
            Paragraph("<b>Safety & Oversight</b>", table_cell_bold),
            Paragraph("Direct auto-send only; no human review mechanism", table_cell),
            Paragraph("'Drafts First' mode, whitelist/blacklist, confidence thresholds", table_cell)
        ],
        [
            Paragraph("<b>Web Server</b>", table_cell_bold),
            Paragraph("Flask built-in development server (Werkzeug)", table_cell),
            Paragraph("Gunicorn WSGI server with Gevent/Gthread behind Nginx / Cloud Run", table_cell)
        ],
        [
            Paragraph("<b>Google Compliance</b>", table_cell_bold),
            Paragraph("Unverified test app (capped at 100 manual test users)", table_cell),
            Paragraph("Verified OAuth App + CASA Tier 2 Security Assessment certified", table_cell)
        ]
    ]

    t_matrix = Table(matrix_data, colWidths=[95, 210, 235])
    t_matrix.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_primary),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    story.append(t_matrix)
    story.append(Spacer(1, 6))

    story.append(Paragraph("6. Pre-Flight Go-Live Launch Checklist", h1_style))
    checklist_items = [
        "[  ] <b>Security:</b> Rotate all developer API keys and verify zero tokens reside in cookies or git history.",
        "[  ] <b>Database:</b> Apply PostgreSQL migration scripts and verify row-level security / tenant constraints.",
        "[  ] <b>OAuth:</b> Complete Google Cloud OAuth verification and ensure consent screen shows verified domain.",
        "[  ] <b>Safety Net:</b> Default all new user accounts to 'Draft Mode' to prevent unintended outbound email replies.",
        "[  ] <b>Billing:</b> Test Stripe webhook events (checkout.session.completed, customer.subscription.deleted).",
        "[  ] <b>Monitoring:</b> Verify Sentry error captures and configure alert thresholds for OpenAI rate limits."
    ]
    for item in checklist_items:
        story.append(Paragraph(item, bullet_style))

    # Build document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated {filename}")

if __name__ == "__main__":
    output_filename = "c:/Users/User/OneDrive/Images/Desktop/metaHackthon/AURA_Production_Website_Roadmap.pdf"
    build_pdf(output_filename)
