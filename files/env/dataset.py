"""
Simulated email dataset for the AI Email Triage System.
Contains spam, work, and personal emails with ground truth labels.
"""

from typing import List, Dict, Any

EMAIL_DATASET: List[Dict[str, Any]] = [
    # ── SPAM EMAILS ──────────────────────────────────────────────────────────
    {
        "id": "spam_001",
        "subject": "CONGRATULATIONS! You've Won $1,000,000!!!",
        "body": (
            "Dear Lucky Winner,\n\n"
            "You have been selected as the GRAND PRIZE WINNER of our international lottery!\n"
            "To claim your $1,000,000 prize, please send your bank account details to "
            "lottery@prize-claim.xyz immediately.\n\n"
            "Act NOW before your prize expires!\n\n"
            "Best regards,\nLottery Commission"
        ),
        "sender": "lottery@prize-claim.xyz",
        "sender_type": "unknown",
        "ground_truth": {
            "classification": "spam",
            "priority": "low",
            "suggested_reply": None,
        },
    },
    {
        "id": "spam_002",
        "subject": "Make $5000/day working from home – NO EXPERIENCE NEEDED",
        "body": (
            "Hi,\n\n"
            "Are you tired of your 9-to-5 job? We have a SECRET system that lets anyone "
            "earn $5000 per day from home. Click the link below to learn more:\n"
            "http://totally-legit-money.biz/free-money\n\n"
            "Limited time offer! Join 50,000 happy members today.\n\n"
            "Cheers,\nWealth Team"
        ),
        "sender": "noreply@totally-legit-money.biz",
        "sender_type": "unknown",
        "ground_truth": {
            "classification": "spam",
            "priority": "low",
            "suggested_reply": None,
        },
    },
    {
        "id": "spam_003",
        "subject": "Your account has been compromised – URGENT action required",
        "body": (
            "SECURITY ALERT!\n\n"
            "Your PayPal account has been accessed from an unknown device. "
            "To secure your account, please verify your details immediately:\n"
            "http://paypal-security-verify.net/login\n\n"
            "Failure to do so will result in permanent account suspension.\n\n"
            "PayPal Security Team"
        ),
        "sender": "security@paypal-security-verify.net",
        "sender_type": "unknown",
        "ground_truth": {
            "classification": "spam",
            "priority": "low",
            "suggested_reply": None,
        },
    },
    {
        "id": "spam_004",
        "subject": "Exclusive offer: 90% OFF designer goods – Today Only!",
        "body": (
            "Flash Sale!\n\n"
            "Get authentic designer bags, watches, and shoes at 90% off retail price. "
            "All items ship worldwide. Visit our website now:\n"
            "www.cheap-luxury-deals.net\n\n"
            "Sale ends tonight!\n\n"
            "The Deals Team"
        ),
        "sender": "deals@cheap-luxury-deals.net",
        "sender_type": "unknown",
        "ground_truth": {
            "classification": "spam",
            "priority": "low",
            "suggested_reply": None,
        },
    },
    # ── WORK / IMPORTANT EMAILS ───────────────────────────────────────────────
    {
        "id": "work_001",
        "subject": "Q3 Financial Report – Review Required Before Board Meeting",
        "body": (
            "Hi Team,\n\n"
            "Please find attached the Q3 financial report for your review. "
            "The board meeting is scheduled for Friday at 2 PM and we need all department "
            "heads to confirm they have reviewed the figures by Thursday EOD.\n\n"
            "Key highlights:\n"
            "- Revenue up 12% YoY\n"
            "- Operating costs reduced by 8%\n"
            "- Q4 projections attached\n\n"
            "Please reply to confirm receipt and indicate if you have any questions.\n\n"
            "Best regards,\nSarah Mitchell\nCFO, Acme Corp"
        ),
        "sender": "sarah.mitchell@acmecorp.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "important",
            "priority": "high",
            "suggested_reply": (
                "Hi Sarah,\n\n"
                "Thank you for sharing the Q3 financial report. I have received and reviewed "
                "the document. The revenue growth and cost reductions are encouraging. "
                "I will be prepared for the board meeting on Friday at 2 PM and have no "
                "outstanding questions at this time.\n\n"
                "Best regards"
            ),
        },
    },
    {
        "id": "work_002",
        "subject": "Action Required: Server maintenance window this Saturday",
        "body": (
            "Dear All,\n\n"
            "Our DevOps team has scheduled a critical server maintenance window this "
            "Saturday from 11 PM to 3 AM. During this time, all production services "
            "will be unavailable.\n\n"
            "Action required:\n"
            "1. Notify your clients about the downtime\n"
            "2. Ensure no scheduled jobs run during this window\n"
            "3. Confirm acknowledgment by replying to this email\n\n"
            "Failure to prepare may result in data loss.\n\n"
            "Regards,\nDevOps Team\nIT Department"
        ),
        "sender": "devops@company.internal",
        "sender_type": "known",
        "ground_truth": {
            "classification": "important",
            "priority": "high",
            "suggested_reply": (
                "Hi DevOps Team,\n\n"
                "Acknowledged. I will notify our clients about the Saturday maintenance window "
                "and ensure no scheduled jobs are running between 11 PM and 3 AM. "
                "Thank you for the advance notice.\n\n"
                "Best regards"
            ),
        },
    },
    {
        "id": "work_003",
        "subject": "Project Phoenix – Weekly Status Update",
        "body": (
            "Hi everyone,\n\n"
            "Here is the weekly status update for Project Phoenix:\n\n"
            "Completed this week:\n"
            "- User authentication module (100%)\n"
            "- Database schema finalized\n\n"
            "In progress:\n"
            "- API integration (60% done, on track)\n"
            "- UI wireframes under review\n\n"
            "Blockers:\n"
            "- Waiting on legal approval for data processing agreement\n\n"
            "Next week: Focus on completing API integration and beginning front-end development.\n\n"
            "Please let me know if you have any concerns.\n\n"
            "Thanks,\nAlex Chen\nProject Manager"
        ),
        "sender": "alex.chen@company.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "important",
            "priority": "medium",
            "suggested_reply": (
                "Hi Alex,\n\n"
                "Thank you for the status update. Good progress on the authentication module "
                "and database schema. Regarding the legal blocker on the data processing "
                "agreement, I will follow up with the legal team to expedite their review. "
                "Looking forward to the API integration completion next week.\n\n"
                "Best regards"
            ),
        },
    },
    {
        "id": "work_004",
        "subject": "Interview scheduling – Software Engineer candidate",
        "body": (
            "Hi,\n\n"
            "We have a strong candidate for the Senior Software Engineer role. "
            "Could you please confirm your availability for a 45-minute technical interview "
            "on one of the following slots?\n\n"
            "- Tuesday 10:00 AM – 10:45 AM\n"
            "- Wednesday 2:00 PM – 2:45 PM\n"
            "- Thursday 11:00 AM – 11:45 AM\n\n"
            "The candidate's CV is attached for your reference.\n\n"
            "Best,\nHR Team"
        ),
        "sender": "hr@company.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "important",
            "priority": "medium",
            "suggested_reply": (
                "Hi HR Team,\n\n"
                "Thank you for reaching out. I am available on Wednesday from 2:00 PM to 2:45 PM "
                "for the technical interview. Please confirm this slot and send me the meeting "
                "invite along with any specific topics I should cover during the interview.\n\n"
                "Best regards"
            ),
        },
    },
    {
        "id": "work_005",
        "subject": "Monthly newsletter – Company updates for October",
        "body": (
            "Hello Team,\n\n"
            "Here are the company highlights for October:\n\n"
            "- New office in Austin opens next month\n"
            "- Annual company retreat scheduled for December 12-14\n"
            "- Employee of the month: Priya Sharma (Engineering)\n"
            "- New health benefits enrollment opens November 1\n\n"
            "Read the full newsletter on the intranet.\n\n"
            "HR Communications Team"
        ),
        "sender": "newsletter@company.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "normal",
            "priority": "low",
            "suggested_reply": None,
        },
    },
    # ── PERSONAL EMAILS ───────────────────────────────────────────────────────
    {
        "id": "personal_001",
        "subject": "Dinner plans this weekend?",
        "body": (
            "Hey!\n\n"
            "Are you free this Saturday evening? A few of us are planning to try that new "
            "Italian restaurant downtown – La Piccola Cucina. They have an amazing tasting menu.\n\n"
            "Let me know if you can make it and I'll make a reservation for around 7 PM.\n\n"
            "Cheers,\nMike"
        ),
        "sender": "mike.johnson@gmail.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "normal",
            "priority": "low",
            "suggested_reply": (
                "Hey Mike,\n\n"
                "That sounds great! I'm free this Saturday evening and would love to join. "
                "7 PM works perfectly for me. Please go ahead and make the reservation – "
                "looking forward to it!\n\n"
                "Cheers"
            ),
        },
    },
    {
        "id": "personal_002",
        "subject": "Flight booking confirmation – NYC trip",
        "body": (
            "Hi,\n\n"
            "Your flight booking is confirmed:\n\n"
            "Flight: AA 2341\n"
            "From: Los Angeles (LAX)\n"
            "To: New York (JFK)\n"
            "Date: November 15, 2024\n"
            "Departure: 8:30 AM\n"
            "Arrival: 5:10 PM\n"
            "Booking reference: AA-78KL23\n\n"
            "Please check in online 24 hours before departure.\n\n"
            "American Airlines"
        ),
        "sender": "noreply@aa.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "normal",
            "priority": "medium",
            "suggested_reply": None,
        },
    },
    {
        "id": "personal_003",
        "subject": "Happy Birthday! 🎉",
        "body": (
            "Dear Friend,\n\n"
            "Wishing you a very happy birthday! Hope you have a wonderful day surrounded "
            "by loved ones. You deserve all the happiness in the world.\n\n"
            "Let's catch up soon – it's been too long!\n\n"
            "Warm wishes,\nEmily"
        ),
        "sender": "emily.watson@gmail.com",
        "sender_type": "known",
        "ground_truth": {
            "classification": "normal",
            "priority": "low",
            "suggested_reply": (
                "Hi Emily,\n\n"
                "Thank you so much for the birthday wishes! It really made my day. "
                "I completely agree – we should definitely catch up soon. "
                "Let's plan something for next week!\n\n"
                "Warm regards"
            ),
        },
    },
]


def get_email_by_id(email_id: str) -> Dict[str, Any]:
    """Retrieve an email from the dataset by its ID."""
    for email in EMAIL_DATASET:
        if email["id"] == email_id:
            return email
    raise ValueError(f"Email ID '{email_id}' not found in dataset.")


def get_emails_by_type(email_type: str) -> List[Dict[str, Any]]:
    """Retrieve emails filtered by prefix (spam, work, personal)."""
    return [e for e in EMAIL_DATASET if e["id"].startswith(email_type)]
