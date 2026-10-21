#!/usr/bin/env python3
"""Debug script to see what prompts are being sent to the LLM."""

import json
from env.gmail_environment import GmailTriageEnv

env = GmailTriageEnv(task_id="task3", email_limit=5)
obs = env.reset()

print("=" * 80)
print("EMAIL EMAIL: SYSTEM PROMPTS AND OBSERVATIONS")
print("=" * 80)
print()

email_obs = obs.current_email
print(f"Email Subject: {email_obs.subject}")
print(f"Email From: {email_obs.sender}")
print(f"Email Type Detected: {email_obs.sender_type}")
print()
print("EMAIL BODY:")
print("-" * 80)
print(email_obs.body)
print("-" * 80)
print()
print(f"Body Length: {len(email_obs.body)} chars")
print()
print("=" * 80)

# Show what the user prompt would look like
print("USER PROMPT THAT WOULD BE SENT TO LLM:")
print("=" * 80)
user_prompt = f"""Process this email:

Subject: {email_obs.subject}
From: {email_obs.sender} ({email_obs.sender_type})
Body:
{email_obs.body}

Classify, assign priority, and generate a reply if appropriate."""

print(user_prompt)
print("=" * 80)
