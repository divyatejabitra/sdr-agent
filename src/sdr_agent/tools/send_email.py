import os
import smtplib
from email.message import EmailMessage


def send_plain_email(
    *,
    to_address: str,
    subject: str,
    body: str,
    from_address: str | None = None,
) -> str:
    """Send one plain-text email via SMTP. Used by the Flow, not by the LLM."""
    if os.getenv("SEND_EMAILS", "").lower() not in {"1", "true", "yes"}:
        return "Skipped send: set SEND_EMAILS=true to actually deliver mail."

    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_address = from_address or os.getenv("SMTP_FROM", username)

    if not host or not from_address or not to_address:
        raise ValueError("SMTP_HOST, from address, and prospect email are required to send.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = to_address
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)

    return f"Sent to {to_address}"
