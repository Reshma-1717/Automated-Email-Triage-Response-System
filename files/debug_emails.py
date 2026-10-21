#!/usr/bin/env python3
"""Debug script to see what emails are being fetched and their content."""

from env.gmail_environment import get_gmail_emails
import json

print("=" * 60)
print("Fetching emails from Gmail...")
print("=" * 60)

emails = get_gmail_emails(limit=5)

for i, email in enumerate(emails, 1):
    print(f"\n[{i}] Email ID: {email['id'][:20]}...")
    print(f"    Subject: {email['subject']}")
    print(f"    Sender: {email['sender']}")
    print(f"    Body Preview: {email['body'][:200]}")
    print(f"    Body Length: {len(email['body'])} chars")

print("\n" + "=" * 60)
print(f"Total emails fetched: {len(emails)}")
print("=" * 60)
