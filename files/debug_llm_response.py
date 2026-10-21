#!/usr/bin/env python3
"""Debug script to see LLM responses."""

import json
import os
import sys
from typing import Any, Dict, List

import openai
from dotenv import load_dotenv

from env.gmail_environment import GmailTriageEnv

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = "gpt-4o-mini"

if not OPENAI_API_KEY:
    print("[ERROR] OPENAI_API_KEY not set")
    sys.exit(1)

client = openai.OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=API_BASE_URL,
)

SYSTEM_PROMPT = """You are an AI email triage assistant. Your job is to process emails
and take structured actions based on what you observe.

For each email, respond with a JSON object containing required actions.

## Action Types
- classify_email:   "spam" | "important" | "normal"
- assign_priority:  "high" | "medium" | "low"
- generate_reply:   a professional reply string (empty string "" ONLY for pure spam/notifications)

## Classification Rules
- spam: unsolicited, phishing, malicious, newsletters
- important: work-related, asks questions, requires action, urgent, feedback requests, meetings
- normal: casual information, status updates, service notifications

## Priority Rules
- high: action needed this week, urgent questions, meeting requests
- medium: needs response, feedback requested, questions asked
- low: FYI only, informational, newsletters

## Reply Rules - IMPORTANT: BE GENEROUS WITH REPLIES!
- Spam only: leave reply as empty string ""
- EMAILS WITH QUESTIONS: MUST generate thoughtful reply addressing every question
- EMAILS ASKING FOR FEEDBACK: MUST generate reply
- EMAILS REQUESTING MEETINGS: MUST generate reply  
- EMAILS SAYING "LET ME KNOW": MUST generate reply
- EMAILS WITH "?": MUST generate reply
- Action request emails: Generate replies (even if just confirming receipt)
- Service notifications/security alerts: empty string ""

If email contains "?", "feedback", "review", "thoughts", "opinion", "when", "schedule", "meeting", or "action" → GENERATE REPLY.
If email is from Gmail/Google/automation → leave empty.
OTHERWISE → generate reply."""

# Set up environment
env = GmailTriageEnv(task_id="task3", email_limit=5)
obs = env.reset()

email = obs.current_email

user_prompt = (
    f"## Email to Process\n"
    f"Subject: {email.subject}\n"
    f"From: {email.sender}\n\n"
    f"Body:\n{email.body}\n\n"
    f"## Required Actions: ['classify_email', 'assign_priority', 'generate_reply']\n\n"
    f"Respond with a JSON object containing only the required actions."
)

print("=" * 80)
print("USER PROMPT SENT TO LLM:")
print("=" * 80)
print(user_prompt)
print()
print("=" * 80)
print("CALLING LLM...")
print("=" * 80)
print()

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": user_prompt},
]

response = client.chat.completions.create(
    model=MODEL_NAME,
    messages=messages,
    temperature=0.0,
    max_tokens=600,
    response_format={"type": "json_object"},
)

llm_response = response.choices[0].message.content or ""

print("RAW LLM RESPONSE:")
print("=" * 80)
print(llm_response)
print("=" * 80)
print()

try:
    data = json.loads(llm_response)
    print("PARSED JSON:")
    print(json.dumps(data, indent=2))
    print()
    print(f"classify_email: {data.get('classify_email')}")
    print(f"assign_priority: {data.get('assign_priority')}")
    print(f"generate_reply: '{data.get('generate_reply')}'")
    print(f"reply length: {len(data.get('generate_reply', ''))}")
except json.JSONDecodeError as e:
    print(f"JSON PARSE ERROR: {e}")
