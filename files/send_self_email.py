#!/usr/bin/env python3
"""Send a test email to yourself to test the auto-reply system."""

import base64
from email.mime.text import MIMEText
from gmail_auth import get_gmail_service
import time

def send_test_email():
    """Send a test email to yourself."""
    try:
        service = get_gmail_service()
        
        # Get user's email address
        profile = service.users().getProfile(userId='me').execute()
        user_email = profile.get('emailAddress')
        
        print(f"📧 Sending test email to: {user_email}")
        print()
        
        # Create test email - IMPORTANT: Clear subject and body that needs a response
        subject = "[ACTION NEEDED] Project Review Feedback Required"
        body = """Hi,

I've been working on this new project and would appreciate your feedback. Could you please review the attached requirements and let me know:

1. Are the project goals clear?
2. Do you have any concerns about the timeline?
3. Can we schedule a meeting next week to discuss?

The sooner we can align on this, the better we can move forward.

Thanks!"""
        
        message = MIMEText(body)
        message['to'] = user_email
        message['subject'] = subject
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_message = {'raw': raw_message}
        
        # Send email
        result = service.users().messages().send(
            userId='me',
            body=send_message
        ).execute()
        
        print(f"✅ Test email sent successfully!")
        print(f"   Message ID: {result['id']}")
        print(f"   Subject: {subject}")
        print(f"   To: {user_email}")
        print()
        print("⏳ Waiting 10 seconds for Gmail to process...")
        time.sleep(10)
        print()
        print("📧 Now run the inference script to auto-reply:")
        print("   python inference_gmail_with_send.py --task task3 --emails 5 --send")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    send_test_email()
