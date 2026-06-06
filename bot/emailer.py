"""
Email notifications for completed trades.
Uses Gmail SMTP with an App Password (no third-party service needed).

Setup:
1. Enable 2FA on your Google account
2. Go to myaccount.google.com > Security > App Passwords
3. Create an app password for "Mail"
4. Add to .env:
   EMAIL_FROM=your@gmail.com
   EMAIL_TO=your@email.com
   EMAIL_SMTP_PASS=xxxx xxxx xxxx xxxx  (the 16-char app password)
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from bot.logger import setup_logger

logger = setup_logger("emailer")

EMAIL_FROM  = os.getenv("EMAIL_FROM", "")
EMAIL_TO    = os.getenv("EMAIL_TO", "")
SMTP_PASS   = os.getenv("EMAIL_SMTP_PASS", "")
SMTP_HOST   = "smtp.gmail.com"
SMTP_PORT   = 587


def _email_enabled() -> bool:
    return bool(EMAIL_FROM and EMAIL_TO and SMTP_PASS)


def send_trade_notification(record: dict, exit_reasoning: str = ""):
    """Send an email notification when a trade closes."""
    if not _email_enabled():
        return

    token      = record.get("token", "?")
    gain_usd   = float(record.get("gain_loss_usd", 0))
    gain_pct   = float(record.get("gain_loss_pct", 0))
    cost       = float(record.get("cost_basis_usd", 0))
    proceeds   = float(record.get("proceeds_usd", 0))
    hold_days  = record.get("hold_days", 0)
    opened     = record.get("date_opened", "")[:16].replace("T", " ")
    closed     = record.get("date_closed", "")[:16].replace("T", " ")
    outcome    = "WIN ✅" if gain_usd >= 0 else "LOSS ❌"
    entry_rsn  = record.get("entry_reasoning", "N/A")

    subject = f"CryptoBot {outcome}: {token} {gain_pct:+.1f}% (${gain_usd:+.2f})"

    body = f"""
CryptoBot Trade Notification
{'='*40}

Token:     {token}
Outcome:   {outcome}
P&L:       ${gain_usd:+.2f} ({gain_pct:+.1f}%)

Cost:      ${cost:.2f}
Proceeds:  ${proceeds:.2f}
Hold time: {hold_days}d ({opened} → {closed})

Entry reason:
{entry_rsn}

Exit reason:
{exit_reasoning or 'N/A'}

{'='*40}
View dashboard: http://143.198.37.28:5000
    """.strip()

    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_FROM, SMTP_PASS)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        logger.info(f"Trade email sent: {subject}")

    except Exception as e:
        logger.warning(f"Failed to send trade email: {e}")
