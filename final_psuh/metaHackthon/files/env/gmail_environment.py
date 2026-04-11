"""
Gmail Email Triage Environment
Fetches real emails from Gmail and applies classification/prioritization logic.
Similar interface to env/environment.py but with real Gmail data.
"""

import base64
import re
from dataclasses import dataclass, field, asdict
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
    """What the agent sees for a Gmail email."""
    email_id: str
    subject: str
    body: str
    sender: str
    sender_type: str  # "unknown" | "known"
    email_index: int
    total_emails: int


@dataclass
class GmailObservation:
    """Observation from Gmail environment."""
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
    """Action for Gmail environment (same as original)."""
    action_type: str
    classification: Optional[str] = None
    priority: Optional[str] = None
    reply: Optional[str] = None


# Gmail helper functions
def get_gmail_emails(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch recent emails from Gmail inbox.
    Returns a list of email dicts with: id, subject, body, sender, sender_type
    """
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
            
            # Extract subject - try harder to find it
            subject = None
            for h in headers:
                if h.get('name', '').lower() == 'subject':
                    subject = h.get('value', '').strip()
                    break
            
            # If subject is still empty, try alternate headers
            if not subject or subject == '':
                # Try x-cleaned-subject or other variations
                for h in headers:
                    if 'subject' in h.get('name', '').lower():
                        subject = h.get('value', '').strip()
                        if subject:
                            break
            
            if not subject or subject == '':
                subject = '(No Subject)'
            
            sender = None
            for h in headers:
                if h.get('name', '').lower() == 'from':
                    sender = h.get('value', '').strip()
                    break
            
            if not sender:
                sender = 'unknown@unknown.com'

            # Extract email body
            body = ""
            if 'parts' in msg_data['payload']:
                # Try text/plain first, then text/html
                for part in msg_data['payload']['parts']:
                    if part['mimeType'] in ['text/plain', 'text/html']:
                        if 'data' in part.get('body', {}):
                            body = base64.urlsafe_b64decode(
                                part['body']['data']
                            ).decode('utf-8', errors='ignore')
                            break
                        elif 'attachmentId' not in part:
                            # Try nested parts for multipart
                            if 'parts' in part:
                                for subpart in part['parts']:
                                    if subpart['mimeType'] == 'text/plain' and 'data' in subpart.get('body', {}):
                                        body = base64.urlsafe_b64decode(
                                            subpart['body']['data']
                                        ).decode('utf-8', errors='ignore')
                                        break
                            break
            
            # Try simple body extraction if parts didn't work
            if not body and 'body' in msg_data['payload'] and 'data' in msg_data['payload']['body']:
                try:
                    body = base64.urlsafe_b64decode(
                        msg_data['payload']['body']['data']
                    ).decode('utf-8', errors='ignore')
                except:
                    body = ""

            # Clean body text - remove extra whitespace and HTML if present
            body = re.sub(r'<[^>]+>', '', body)  # Remove HTML tags
            body = ' '.join(body.split())  # Normalize whitespace
            body = body[:500]  # Limit to 500 chars for processing

            # Determine sender type (very basic heuristic)
            sender_type = "unknown"
            if any(domain in sender for domain in ['@yourcompany.com', '@company.com']):
                sender_type = "known"

            emails.append({
                "id": msg['id'],
                "subject": subject,
                "body": body,
                "sender": sender,
                "sender_type": sender_type,
                "ground_truth": {  # Placeholder - will be filled by AI
                    "classification": "normal",
                    "priority": "medium",
                    "suggested_reply": None,
                }
            })

        return emails

    except Exception as e:
        print(f"[ERROR] Failed to fetch Gmail emails: {e}")
        return []


class GmailTriageEnv:
    """
    Email Triage Environment for real Gmail emails.
    Same interface as EmailTriageEnv but pulls from Gmail.
    """

    def __init__(self, task_id: str = "task1", email_limit: int = 10):
        self.task_id = task_id
        self.email_limit = email_limit

        # Internal state
        self._emails: List[Dict[str, Any]] = []
        self._email_index: int = 0
        self._actions: Dict[str, Dict[str, Any]] = {}
        self._step_count: int = 0
        self._total_reward: float = 0.0
        self._done: bool = False
        self._initialised: bool = False

        # Task configs
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
        """Initialize and fetch emails from Gmail."""
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
        """
        Process an action on the current email.
        Returns (observation, reward, done, info).
        """
        if not self._initialised:
            raise RuntimeError("Call reset() before step().")

        self._step_count += 1
        reward = 0.0
        feedback = ""
        info: Dict[str, Any] = {}

        # Get current email
        if self._email_index >= len(self._emails):
            if action.action_type == "finish":
                return self._finalise(info)
            return self._build_observation("All emails processed."), 0.0, True, {}

        current_id = self._emails[self._email_index]["id"]

        # Handle actions
        if action.action_type == "finish":
            return self._finalise(info)

        if action.action_type == "classify_email":
            if current_id not in self._actions:
                self._actions[current_id] = {}

            cls = (action.classification or "").strip().lower()
            if cls not in {"spam", "important", "normal"}:
                reward = -0.10
                feedback = f"Invalid classification: {cls}"
            else:
                self._actions[current_id]["classification"] = cls
                reward = 0.40
                feedback = f"Classified as: {cls}"

        elif action.action_type == "assign_priority":
            if current_id not in self._actions:
                self._actions[current_id] = {}

            prio = (action.priority or "").strip().lower()
            if prio not in {"high", "medium", "low"}:
                reward = -0.10
                feedback = f"Invalid priority: {prio}"
            else:
                self._actions[current_id]["priority"] = prio
                reward = 0.30
                feedback = f"Priority set to: {prio}"

        elif action.action_type == "generate_reply":
            if current_id not in self._actions:
                self._actions[current_id] = {}

            reply = (action.reply or "").strip()
            self._actions[current_id]["reply"] = reply
            reward = 0.30 if len(reply) > 20 else 0.1
            feedback = f"Reply generated ({len(reply)} chars)"

        elif action.action_type == "skip_email":
            reward = -0.20
            feedback = "Email skipped."
            self._email_index += 1
            if self._email_index >= len(self._emails):
                return self._finalise(info)
            return self._build_observation(feedback), reward, False, {}

        self._total_reward += reward

        # Auto-advance to next email when actions complete
        if self._task_id_to_required_actions(self.task_id).issubset(
            set(self._actions.get(current_id, {}).keys())
        ):
            self._email_index += 1
            feedback += " → Next email."
            if self._email_index >= len(self._emails):
                obs, _, _, fin_info = self._finalise(info)
                fin_info["step_feedback"] = feedback
                return obs, reward, True, fin_info

        obs = self._build_observation(feedback)
        return obs, reward, self._done, info

    def state(self) -> Dict[str, Any]:
        """Return current state."""
        return {
            "task_id": self.task_id,
            "emails": len(self._emails),
            "email_index": self._email_index,
            "actions": self._actions,
            "step_count": self._step_count,
            "total_reward": self._total_reward,
            "done": self._done,
        }

    # Internal helpers

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
        """Return required action keys for this task."""
        if task_id == "task1":
            return {"classification"}
        elif task_id == "task2":
            return {"classification", "priority"}
        else:
            return {"classification", "priority", "reply"}

    def _finalise(self, info: Dict[str, Any]) -> Tuple[GmailObservation, float, bool, Dict[str, Any]]:
        """Finalize episode."""
        self._done = True

        # Build mock ground truths (for compatibility)
        ground_truths = {}
        email_bodies = {}
        for email in self._emails:
            ground_truths[email["id"]] = email.get("ground_truth", {
                "classification": "normal",
                "priority": "medium",
                "suggested_reply": None
            })
            email_bodies[email["id"]] = email.get("body", "")

        # Grade based on task
        if self.task_id == "task1":
            score, breakdown = grade_task1(self._actions, ground_truths)
        elif self.task_id == "task2":
            score, breakdown = grade_task2(self._actions, ground_truths)
        else:
            score, breakdown = grade_task3(self._actions, ground_truths, email_bodies)

        # Apply efficiency penalty
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
