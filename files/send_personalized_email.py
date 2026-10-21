#!/usr/bin/env python3
"""Send personalized emails to specific recipients using AI-generated content."""

import argparse
import base64
import os
import sys
from email.mime.text import MIMEText
from typing import List, Dict

import openai
from dotenv import load_dotenv

from gmail_auth import get_gmail_service

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
API_BASE_URL = os.getenv("API_BASE_URL", "https://openai.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

if not OPENAI_API_KEY:
    print("[ERROR] OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
    sys.exit(1)

client = openai.OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=API_BASE_URL,
)

def generate_email_content(recipient_name: str, occasion: str, details: str = "") -> str:
    """Generate personalized email content using AI."""
    prompt = f"""Write a professional and warm congratulation email for the following:

Recipient Name: {recipient_name}
Occasion: {occasion}
Additional Details: {details if details else "None"}

Guidelines:
- Keep it concise (2-3 paragraphs)
- Be genuine and warm
- Make it personal and specific
- Use professional but friendly tone
- No formal signatures, just the body content

Generate ONLY the email body, nothing else."""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        print(f"[ERROR] Failed to generate email content: {e}")
        return ""

def send_email(recipient_email: str, recipient_name: str, subject: str, body: str) -> bool:
    """Send an email to a specific recipient."""
    try:
        service = get_gmail_service()
        
        # Create message
        message = MIMEText(body)
        message['to'] = recipient_email
        message['subject'] = subject
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_message = {'raw': raw_message}
        
        # Send via Gmail API
        result = service.users().messages().send(
            userId='me',
            body=send_message
        ).execute()
        
        return True, result['id']
        
    except Exception as e:
        return False, str(e)

def main():
    parser = argparse.ArgumentParser(
        description="Send personalized emails to specific recipients using AI"
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Recipient email address"
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Recipient name"
    )
    parser.add_argument(
        "--subject",
        required=True,
        help="Email subject line"
    )
    parser.add_argument(
        "--occasion",
        required=True,
        help="What are you congratulating them for? (e.g., 'Got promoted to Senior Manager')"
    )
    parser.add_argument(
        "--details",
        default="",
        help="Additional details to personalize the message"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview the email without sending"
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🎉 AI EMAIL COMPOSER")
    print("=" * 70)
    print()
    
    # Generate email content
    print(f"📝 Generating email for {args.name}...")
    body = generate_email_content(args.name, args.occasion, args.details)
    
    if not body:
        print("[ERROR] Failed to generate email content")
        sys.exit(1)
    
    print()
    print("=" * 70)
    print("📧 EMAIL PREVIEW")
    print("=" * 70)
    print(f"To: {args.email} ({args.name})")
    print(f"Subject: {args.subject}")
    print("-" * 70)
    print(body)
    print("-" * 70)
    print()
    
    # Send or preview
    if args.preview:
        print("✅ Preview mode - email NOT sent")
        return
    
    # Confirm before sending
    confirm = input("Send this email? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("❌ Email not sent")
        return
    
    print()
    print("📧 Sending email...")
    success, result = send_email(args.email, args.name, args.subject, body)
    
    if success:
        print(f"✅ Email sent successfully!")
        print(f"   Message ID: {result}")
        print(f"   To: {args.name} <{args.email}>")
        print(f"   Subject: {args.subject}")
    else:
        print(f"❌ Failed to send email: {result}")
        sys.exit(1)

if __name__ == '__main__':
    main()
