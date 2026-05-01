import os
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'GetFit <noreply@resend.dev>')


def send_password_reset_email(*, to_email: str, user_name: str, reset_url: str) -> None:
    subject = 'Reset your GetFit password'
    text_body = (
        f'Hi {user_name},\n\n'
        f'Someone requested a password reset for your GetFit account.\n\n'
        f'Click the link below to set a new password:\n{reset_url}\n\n'
        f'This link expires in 1 hour. If you didn\'t request this, you can safely ignore this email.\n\n'
        f'— The GetFit team'
    )

    if not RESEND_API_KEY:
        print(
            f"\n{'='*50}\n"
            f'PASSWORD RESET EMAIL (no RESEND_API_KEY set)\n'
            f'To: {to_email}\n'
            f'Subject: {subject}\n\n'
            f'{text_body}\n'
            f"{'='*50}\n",
            flush=True,
        )
        return

    payload = json.dumps({
        'from': FROM_EMAIL,
        'to': [to_email],
        'subject': subject,
        'text': text_body,
    }).encode()

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {RESEND_API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as resp:
            logger.info('Password reset email sent to %s (status %s)', to_email, resp.status)
    except urllib.error.HTTPError as exc:
        logger.error('Failed to send password reset email to %s: %s', to_email, exc)
