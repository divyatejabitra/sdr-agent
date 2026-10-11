import os
import smtplib
from email.message import EmailMessage
from html import escape

import httpx


def _plain_text_to_html(text: str) -> str:
    """Same deterministic wrap sdr_workspace's render_message() uses - plain
    paragraphs, no LLM call. Porting 2_openai/3_lab3.ipynb's html_converter
    agent here would cost an extra LLM call per send for a transform this
    reliable without one.
    """
    return (
        "<html><body>"
        + "".join(f"<p>{escape(line)}</p>" if line else "<br/>" for line in text.splitlines())
        + "</body></html>"
    )


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

    return f"Sent to {to_address} via SMTP"


def send_html_email(
    *,
    to_address: str,
    subject: str,
    html_body: str,
    text_body: str,
    from_address: str | None = None,
) -> str:
    """Send one HTML (+ plain-text fallback) email via SendGrid's REST API.

    Same request shape as sdr_workspace/providers/mail.py's SendGridMail -
    duplicated rather than imported, since sdr_workspace depends on
    sdr_agent, not the other way around, and this is a dozen lines of
    already-verified REST, not shared state.
    """
    if os.getenv("SEND_EMAILS", "").lower() not in {"1", "true", "yes"}:
        return "Skipped send: set SEND_EMAILS=true to actually deliver mail."

    api_key = os.getenv("SENDGRID_API_KEY", "")
    from_address = from_address or os.getenv("SENDGRID_FROM_EMAIL") or os.getenv("SMTP_FROM", "")

    if not api_key or not from_address or not to_address:
        raise ValueError("SENDGRID_API_KEY, from address, and prospect email are required to send via SendGrid.")

    payload = {
        "personalizations": [{"to": [{"email": to_address}]}],
        "from": {"email": from_address},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code not in {200, 202}:
        raise RuntimeError(f"SendGrid send failed: HTTP {response.status_code} {response.text[:300]}")

    return f"Sent to {to_address} via SendGrid"


def send_email(
    *,
    to_address: str,
    subject: str,
    body: str,
    from_address: str | None = None,
) -> str:
    """Send the winning draft - via SendGrid as HTML when SENDGRID_API_KEY is
    set (graceful upgrade, no config change needed for anyone who already
    has the key, same philosophy as _llm_for's provider fallback), else over
    plain SMTP exactly as before.
    """
    if os.getenv("SENDGRID_API_KEY"):
        return send_html_email(
            to_address=to_address,
            subject=subject,
            html_body=_plain_text_to_html(body),
            text_body=body,
            from_address=from_address,
        )
    return send_plain_email(to_address=to_address, subject=subject, body=body, from_address=from_address)
