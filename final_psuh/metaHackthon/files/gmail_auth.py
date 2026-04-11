"""
Gmail OAuth 2.0 Authentication Handler
Manages authentication and token refresh for Gmail API access.
"""

import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send'
]
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.pickle'


def authenticate_gmail():
    """
    Authenticate with Gmail API using OAuth 2.0.
    Returns a Gmail service object.
    """
    creds = None

    # Load existing token if available
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, refresh or create new
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            print("Starting OAuth 2.0 flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save token for future use
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
        print("✅ Authentication successful! Token saved.")

    # Build Gmail service
    from googleapiclient.discovery import build
    service = build('gmail', 'v1', credentials=creds)
    return service


def get_gmail_service():
    """Get authenticated Gmail service."""
    return authenticate_gmail()


def send_reply(message_id: str, reply_text: str, to_email: str, subject: str) -> bool:
    """
    Send a reply to a Gmail message.
    
    Args:
        message_id: Gmail message ID to reply to
        reply_text: The reply text to send
        to_email: Recipient email address
        subject: Original email subject (will be prefixed with "Re: ")
    
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        import base64
        from email.mime.text import MIMEText
        
        service = get_gmail_service()
        
        # Create the reply message
        reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
        message = MIMEText(reply_text)
        message['to'] = to_email
        message['subject'] = reply_subject
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        # Send via Gmail API
        send_message = {
            'raw': raw_message,
            'threadId': message_id  # Reply in the same thread
        }
        
        result = service.users().messages().send(
            userId='me',
            body=send_message
        ).execute()
        
        print(f"✅ Reply sent! Message ID: {result['id']}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send reply: {e}")
        return False
