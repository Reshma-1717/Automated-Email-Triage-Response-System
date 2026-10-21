"""
Gmail Email Triage Environment
Fetches real emails from Gmail and applies classification/prioritization logic.
"""

import base64
import re
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple
from gmail_auth import get_gmail_service
from env.grader import (
    grade_task1,
    grade_task2,
    grade_task3,
    apply_efficiency_penalty,
)


@dataclass
class GmailEmailObservation:
    email_id: str
    subject: str
    body: str
    sender: str
    sender_type: str
    email_index: int
    total_emails: int
    thread_id: str = ""


@dataclass
class GmailObservation:
    task_id: str
    task_name: str
    task_description: str
    difficulty: str
    current_email: Optional[GmailEmailObservation]
    emails_processed: int
    emails_remaining: int
    last_action_feedback: str
    valid_actions: List[str]
    done: bool


@dataclass
class GmailAction:
    action_type: str
    classification: Optional[str] = None
    priority: Optional[str] = None
    reply: Optional[str] = None


# ─── No-reply / automated sender block list ───────────────────────────────────

NO_REPLY_PATTERNS = [
    "no-reply", "noreply", "mailer-daemon", "do-not-reply", "donotreply",
    "accounts.google.com", "notifications@", "alert@", "alerts@",
    "automated@", "system@", "support@", "internshala", "linkedin",
    "newsletter", "team@", "hello@", "info@", "security@",
    "update@", "updates@", "digest@", "bounce@", "postmaster@",
]

def is_automated_sender(sender: str) -> bool:
    """Return True if sender looks like an automated/no-reply address."""
    sender_lower = sender.lower()
    return any(pattern in sender_lower for pattern in NO_REPLY_PATTERNS)


# ─── Gmail helpers ─────────────────────────────────────────────────────────────

def get_gmail_emails(limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch recent emails from Gmail inbox."""
    try:
        service = get_gmail_service()
        results = service.users().messages().list(
            userId='me',
            q='label:INBOX',
            maxResults=limit
        ).execute()

        messages = results.get('messages', [])
        emails = []

        for msg in messages:
            msg_data = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='full'
            ).execute()

            headers = msg_data['payload'].get('headers', [])

            subject = next(
                (h['value'].strip() for h in headers
                 if h.get('name', '').lower() == 'subject'),
                '(No Subject)'
            ) or '(No Subject)'

            sender = next(
                (h['value'].strip() for h in headers
                 if h.get('name', '').lower() == 'from'),
                'unknown@unknown.com'
            )

            message_id_header = next(
                (h['value'].strip() for h in headers
                 if h.get('name', '').lower() == 'message-id'),
                ''
            )

            thread_id = msg_data.get('threadId', msg['id'])

            body = _extract_body(msg_data['payload'])
            body = re.sub(r'<[^>]+>', '', body)
            body = ' '.join(body.split())
            body = body[:500]

            sender_type = "known" if any(
                d in sender for d in ['@yourcompany.com', '@company.com']
            ) else "unknown"

            emails.append({
                "id": msg['id'],
                "thread_id": thread_id,
                "message_id_header": message_id_header,
                "subject": subject,
                "body": body,
                "sender": sender,
                "sender_type": sender_type,
                "ground_truth": {
                    "classification": "normal",
                    "priority": "medium",
                    "suggested_reply": None,
                }
            })

        return emails

    except Exception as e:
        print(f"[ERROR] Failed to fetch Gmail emails: {e}")
        return []


def _extract_body(payload: Dict) -> str:
    """Recursively extract plain text body from Gmail payload."""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part.get('body', {}).get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            if 'parts' in part:
                result = _extract_body(part)
                if result:
                    return result
        for part in payload['parts']:
            if part['mimeType'] == 'text/html':
                data = part.get('body', {}).get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    else:
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return ""


def send_gmail_reply(
    to_email: str,
    subject: str,
    reply_text: str,
    thread_id: str,
    message_id_header: str,
) -> bool:
    """Send a reply via Gmail API, threaded correctly."""
    try:
        service = get_gmail_service()

        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"

        message = MIMEText(reply_text)
        message['to'] = to_email
        message['subject'] = reply_subject
        if message_id_header:
            message['In-Reply-To'] = message_id_header
            message['References'] = message_id_header

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        result = service.users().messages().send(
            userId='me',
            body={
                'raw': raw,
                'threadId': thread_id,
            }
        ).execute()

        print(f"    📨 Reply sent → {to_email} (Message ID: {result['id']})")
        return True

    except Exception as e:
        print(f"    ❌ Failed to send reply: {e}")
        return False


# ─── Environment ───────────────────────────────────────────────────────────────

class GmailTriageEnv:
    """Email Triage Environment for real Gmail emails."""

    def __init__(self, task_id: str = "task1", email_limit: int = 10):
        self.task_id = task_id
        self.email_limit = email_limit

        self._emails: List[Dict[str, Any]] = []
        self._email_index: int = 0
        self._actions: Dict[str, Dict[str, Any]] = {}
        self._step_count: int = 0
        self._total_reward: float = 0.0
        self._done: bool = False
        self._initialised: bool = False

        self._task_name = {
            "task1": "Email Classification",
            "task2": "Email Classification + Priority",
            "task3": "Email Triage: Classify, Prioritize & Reply"
        }.get(task_id, "Email Triage")

        self._task_description = {
            "task1": "Classify each email as spam, important, or normal.",
            "task2": "Classify each email and assign priority (high, medium, low).",
            "task3": "Classify, prioritize, and generate replies for emails."
        }.get(task_id, "Process emails")

    def reset(self) -> GmailObservation:
        print("\n📧 Fetching emails from Gmail...")
        self._emails = get_gmail_emails(self.email_limit)

        if not self._emails:
            print("[ERROR] No emails fetched from Gmail!")
            return self._build_observation("Failed to fetch Gmail emails.")

        print(f"✅ Fetched {len(self._emails)} emails from Gmail inbox")

        self._email_index = 0
        self._actions = {}
        self._step_count = 0
        self._total_reward = 0.0
        self._done = False
        self._initialised = True

        return self._build_observation(f"Loaded {len(self._emails)} emails from Gmail.")

    def step(
        self, action: GmailAction
    ) -> Tuple[GmailObservation, float, bool, Dict[str, Any]]:
        if not self._initialised:
            raise RuntimeError("Call reset() before step().")

        self._step_count += 1
        reward = 0.0
        feedback = ""
        info: Dict[str, Any] = {}

        if self._email_index >= len(self._emails):
            if action.action_type == "finish":
                return self._finalise(info)
            return self._build_observation("All emails processed."), 0.0, True, {}

        current_email = self._emails[self._email_index]
        current_id = current_email["id"]

        if action.action_type == "finish":
            return self._finalise(info)

        if current_id not in self._actions:
            self._actions[current_id] = {}

        if action.action_type == "classify_email":
            cls = (action.classification or "").strip().lower()
            if cls not in {"spam", "important", "normal"}:
                reward = -0.10
                feedback = f"Invalid classification: {cls}"
            else:
                self._actions[current_id]["classification"] = cls
                reward = 0.40
                feedback = f"Classified as: {cls}"

        elif action.action_type == "assign_priority":
            prio = (action.priority or "").strip().lower()
            if prio not in {"high", "medium", "low"}:
                reward = -0.10
                feedback = f"Invalid priority: {prio}"
            else:
                self._actions[current_id]["priority"] = prio
                reward = 0.30
                feedback = f"Priority set to: {prio}"

        elif action.action_type == "generate_reply":
            reply_text = (action.reply or "").strip()
            classification = self._actions[current_id].get("classification", "normal")
            sender = current_email.get("sender", "")

            self._actions[current_id]["reply"] = reply_text

            # ✅ LAYER 2: Hard block — automated/spam senders never get replies
            if classification == "spam" or is_automated_sender(sender):
                reward = 0.10
                reason = "spam" if classification == "spam" else "automated sender"
                feedback = f"Reply blocked ({reason}) ✅"
                print(f"    🚫 Reply blocked → {sender[:50]} [{reason}]")

            elif reply_text and len(reply_text) > 20:
                reward = 0.30
                feedback = f"Reply generated ({len(reply_text)} chars)"
                sent = send_gmail_reply(
                    to_email=sender,
                    subject=current_email["subject"],
                    reply_text=reply_text,
                    thread_id=current_email["thread_id"],
                    message_id_header=current_email["message_id_header"],
                )
                feedback += " — reply sent ✅" if sent else " — reply failed ❌"

            else:
                reward = 0.10
                feedback = "No reply needed or reply too short."

        elif action.action_type == "skip_email":
            reward = -0.20
            feedback = "Email skipped."
            self._email_index += 1
            self._total_reward += reward
            if self._email_index >= len(self._emails):
                return self._finalise(info)
            return self._build_observation(feedback), reward, False, {}

        self._total_reward += reward

        # Auto-advance when all required actions done
        required = self._task_id_to_required_actions(self.task_id)
        if required.issubset(set(self._actions.get(current_id, {}).keys())):
            self._email_index += 1
            feedback += " → Next email."
            if self._email_index >= len(self._emails):
                obs, _, _, fin_info = self._finalise(info)
                fin_info["step_feedback"] = feedback
                return obs, reward, True, fin_info

        return self._build_observation(feedback), reward, self._done, info

    def state(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "emails": len(self._emails),
            "email_index": self._email_index,
            "actions": self._actions,
            "step_count": self._step_count,
            "total_reward": self._total_reward,
            "done": self._done,
        }

    def _current_email(self) -> Optional[Dict[str, Any]]:
        if self._email_index < len(self._emails):
            return self._emails[self._email_index]
        return None

    def _build_observation(self, feedback: str) -> GmailObservation:
        email = self._current_email()
        if email:
            current_obs = GmailEmailObservation(
                email_id=email["id"],
                subject=email["subject"],
                body=email["body"],
                sender=email["sender"],
                sender_type=email["sender_type"],
                email_index=self._email_index,
                total_emails=len(self._emails),
                thread_id=email.get("thread_id", ""),
            )
        else:
            current_obs = None

        return GmailObservation(
            task_id=self.task_id,
            task_name=self._task_name,
            task_description=self._task_description,
            difficulty="medium",
            current_email=current_obs,
            emails_processed=self._email_index,
            emails_remaining=max(0, len(self._emails) - self._email_index),
            last_action_feedback=feedback,
            valid_actions=["classify_email", "assign_priority", "generate_reply", "skip_email", "finish"],
            done=self._done,
        )

    def _task_id_to_required_actions(self, task_id: str) -> set:
        if task_id == "task1":
            return {"classification"}
        elif task_id == "task2":
            return {"classification", "priority"}
        else:
            return {"classification", "priority", "reply"}

    def _finalise(
        self, info: Dict[str, Any]
    ) -> Tuple[GmailObservation, float, bool, Dict[str, Any]]:
        self._done = True

        ground_truths = {}
        email_bodies = {}
        for email in self._emails:
            ground_truths[email["id"]] = email.get("ground_truth", {
                "classification": "normal",
                "priority": "medium",
                "suggested_reply": None
            })
            email_bodies[email["id"]] = email.get("body", "")

        if self.task_id == "task1":
            score, breakdown = grade_task1(self._actions, ground_truths)
        elif self.task_id == "task2":
            score, breakdown = grade_task2(self._actions, ground_truths)
        else:
            score, breakdown = grade_task3(self._actions, ground_truths, email_bodies)

        final_score = apply_efficiency_penalty(score, self._step_count, max_steps=100)
        bonus = 1.0 * final_score
        self._total_reward += bonus

        info.update({
            "grader_score": final_score,
            "grader_breakdown": breakdown,
            "total_reward": self._total_reward,
            "steps_taken": self._step_count,
        })

        obs = self._build_observation(f"✅ Task complete. Score: {final_score:.4f}")
        return obs, bonus, True, info