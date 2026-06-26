#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIGEST STAMPA - EMAIL
=====================
Compone la rassegna del giorno in due sezioni (Novita' Italia / Novita' estero),
raggruppata per testata, con titolo + sommario della testata + link. Nessuna
sintesi AI. Invia via Gmail SMTP. Salva sempre un'anteprima in data/.

Variabili d'ambiente (GitHub Secrets):
  GMAIL_USER, GMAIL_APP_PASSWORD, DIGEST_TO  (vedi digest normativo)
  SEND_IF_EMPTY  "true" per inviare anche senza novita' (default: no)
"""

from __future__ import annotations

import os
import sys
import re
import ssl
import html
import json
import glob
import smtplib
import datetime
from collections import OrderedDict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465

_MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
         "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def _today() -> str:
    return datetime.date.today().isoformat()


def _format_date_it(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.date.fromisoformat(iso)
        return f"{d.day} {_MESI[d.month]} {d.year}"
    except Exception:
        return ""


def _strip_md(t: str) -> str:
    if not t:
        return ""
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)
    t = t.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", t).strip()


def _latest_digest() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "digest_*.json")))
    files = [f for f in files if "summarized" not in f]
    return Path(files[-1]) if files else None


def _group_by_source(items: list[dict]) -> "OrderedDict[str, list]":
    groups: "OrderedDict[str, list]" = OrderedDict()
    for it in items:
        groups.setdefault(it.get("source_name", "—"), []).append(it)
    return groups


def _render_section_html(titolo: str, items: list[dict]) -> str:
    if not items:
        return ""
    parts = [f'<h2 style="font-size:16px;text-transform:uppercase;letter-spacing:.5px;'
             f'color:#1a1a1a;border-bottom:2px solid #1a1a1a;padding-bottom:4px;'
             f'margin:28px 0 16px;">{html.escape(titolo)}</h2>']
    for source, lst in _group_by_source(items).items():
        parts.append(f'<div style="font-size:12px;font-weight:700;color:#666;'
                     f'text-transform:uppercase;letter-spacing:.3px;margin:14px 0 6px;">'
                     f'{html.escape(source)}</div>')
        for it in lst:
            title = html.escape(it.get("title", ""))
            link = html.escape(it.get("link", ""))
            blurb = html.escape(_strip_md(it.get("summary_raw", "")))
            date_it = _format_date_it(it.get("published"))
            datehtml = (f'<span style="color:#999;font-size:11px;"> &middot; {date_it}</span>'
                        if date_it else "")
            blurbhtml = (f'<div style="font-size:13px;color:#444;margin:2px 0 0;">{blurb}</div>'
                         if blurb else "")
            parts.append(f"""<div style="margin:0 0 12px;">
              <a href="{link}" style="font-size:15px;font-weight:600;color:#1a56db;
                 text-decoration:none;">{title}</a>{datehtml}
              {blurbhtml}</div>""")
    return "\n".join(parts)


def render_html(digest: dict) -> str:
    ita = digest.get("ita", [])
    estero = digest.get("estero", [])
    date = digest.get("generated_at", _today())
    parts = [f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
        max-width:680px;margin:0 auto;color:#1a1a1a;line-height:1.5;">
      <h1 style="font-size:20px;margin:0 0 4px;">Rassegna stampa</h1>
      <div style="color:#666;font-size:13px;margin-bottom:8px;">{date} &middot;
        {len(ita)} Italia &middot; {len(estero)} estero</div>"""]
    parts.append(_render_section_html("Novità Italia", ita))
    parts.append(_render_section_html("Novità estero", estero))
    parts.append('<div style="color:#aaa;font-size:11px;margin-top:30px;">'
                 'Titoli e sommari sono delle rispettive testate. Il link porta alla fonte.</div></div>')
    return "\n".join(parts)


def render_text(digest: dict) -> str:
    lines = [f"RASSEGNA STAMPA - {digest.get('generated_at', _today())}", ""]
    for label, key in (("== NOVITA' ITALIA ==", "ita"), ("== NOVITA' ESTERO ==", "estero")):
        items = digest.get(key, [])
        if not items:
            continue
        lines.append(label)
        for source, lst in _group_by_source(items).items():
            lines.append(f"-- {source} --")
            for it in lst:
                lines.append(f"* {it.get('title','')}")
                blurb = _strip_md(it.get("summary_raw", ""))
                if blurb:
                    lines.append(f"  {blurb}")
                lines.append(f"  {it.get('link','')}")
            lines.append("")
    return "\n".join(lines)


def send_gmail(user, app_password, to_addr, subject, text_body, html_body) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as s:
        s.login(user, app_password)
        s.sendmail(user, [to_addr], msg.as_string())


def run() -> int:
    path = _latest_digest()
    if not path:
        print("ERRORE: nessun digest_*.json. Esegui prima process.py.")
        return 1
    digest = json.loads(path.read_text(encoding="utf-8"))
    ita, estero = digest.get("ita", []), digest.get("estero", [])

    html_body, text_body = render_html(digest), render_text(digest)
    preview = DATA_DIR / f"email_{_today()}.html"
    preview.write_text(html_body, encoding="utf-8")
    print(f"Anteprima salvata: {preview.relative_to(ROOT)}")

    send_if_empty = os.environ.get("SEND_IF_EMPTY", "").strip().lower() in ("1", "true", "yes")
    if not ita and not estero and not send_if_empty:
        print("Nessuna novita' oggi: email non inviata.")
        return 0

    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to_addr = os.environ.get("DIGEST_TO", "").strip() or user
    if not user or not pw:
        print("ATTENZIONE: GMAIL_USER / GMAIL_APP_PASSWORD non impostati: email NON inviata.")
        return 0

    subject = f"Rassegna stampa - {_today()} ({len(ita)} IT / {len(estero)} EST)"
    try:
        send_gmail(user, pw, to_addr, subject, text_body, html_body)
        print(f"Email inviata a {to_addr}.")
    except Exception as ex:
        print(f"ERRORE invio email: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
