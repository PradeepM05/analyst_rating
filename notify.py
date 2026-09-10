"""Email the daily digest via Gmail SMTP.

Needs in .env / environment:
    GMAIL_ADDRESS=pradeep.dst1@gmail.com
    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   (App Password, NOT your real password:
                                           Google Account -> Security -> 2-Step
                                           Verification -> App passwords)
DIGEST_TO defaults to GMAIL_ADDRESS (send to self).

CLI:
    python notify.py                  # email today's digest file
    python notify.py --date 2026-09-08
"""
import argparse
import os
import smtplib
import ssl
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config  # noqa: F401  (side effect: loads .env)

DIGEST_DIR = Path(__file__).parent / "digests"


def md_to_html(md: str) -> str:
    """Minimal markdown -> HTML good enough for a digest email. No dependencies."""
    import html as h
    import re
    lines = []
    for line in md.splitlines():
        raw = line
        line = h.escape(line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        line = re.sub(r"_(.+?)_", r"<i>\1</i>", line)
        if raw.startswith("# "):
            lines.append(f"<h2>{line[2:]}</h2>")
        elif raw.startswith("## "):
            lines.append(f"<h3>{line[3:]}</h3>")
        elif raw.startswith("  > "):
            lines.append(f"<div style='margin-left:1em;color:#555'>{line[4:]}</div>")
        elif raw.strip() == "---":
            lines.append("<hr>")
        elif raw.strip() == "":
            lines.append("<br>")
        else:
            lines.append(f"<div>{line}</div>")
    return ("<html><body style='font-family:Segoe UI,Arial,sans-serif;"
            "font-size:14px;line-height:1.5'>" + "\n".join(lines) + "</body></html>")


def send_digest(target_date: str = None) -> None:
    d = target_date or date.today().isoformat()
    path = DIGEST_DIR / f"digest_{d}.md"
    if not path.exists():
        raise SystemExit(f"no digest at {path} — run digest.py first")

    sender = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = os.environ.get("DIGEST_TO", sender)
    if not sender or not password:
        raise SystemExit("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set (put them in .env)")

    md = path.read_text(encoding="utf-8")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Analyst Ratings Digest — {d}"
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(md, "plain", "utf-8"))
    msg.attach(MIMEText(md_to_html(md), "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(sender, password)
        server.sendmail(sender, [to], msg.as_string())
    print(f"emailed digest to {to}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default today)")
    args = ap.parse_args()
    send_digest(args.date)
