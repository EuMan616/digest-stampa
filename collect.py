#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FASE 1 - RACCOLTA
=================
Legge sources.yaml, interroga ogni fonte, normalizza tutto in un formato unico,
salva data/collected_<data>.json e stampa un riepilogo per fonte.

Principi:
- non si blocca mai se una singola fonte fallisce: la segnala e prosegue;
- la prima esecuzione serve a VERIFICARE gli URL: per ogni fonte stampa cosa ha
  provato e cosa ha ottenuto, cosi' si capisce quali feed sono corretti;
- per la fonte "discovery" (dirittobancario) salva SOLO titolo e link, mai il
  testo (il loro riassunto e' protetto da copyright).

Si lancia senza argomenti:  python collect.py
"""

from __future__ import annotations

import sys
import json
import re
import hashlib
import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import yaml
import requests
import feedparser
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"
DATA_DIR = ROOT / "data"

# User-Agent onesto: gli enti pubblici gradiscono sapere chi li interroga.
USER_AGENT = "digest-normativo/0.1 (uso personale)"
TIMEOUT = 30          # secondi per richiesta
MAX_ITEMS_PER_SOURCE = 60   # taglio di sicurezza: ci interessano le novita' recenti


# --------------------------------------------------------------------------- #
#  Modello dato unico
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    source_id: str
    source_name: str
    title: str
    link: str
    published: Optional[str]   # data ISO 'YYYY-MM-DD' se disponibile, altrimenti None
    summary_raw: str           # estratto grezzo dalla fonte (NON il riassunto AI finale)
    item_type: str             # rss | scrape | discovery | eurlex
    fetched_at: str

    def uid(self) -> str:
        return hashlib.sha1(f"{self.source_id}|{self.link}".encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------------------- #
#  Utilita'
# --------------------------------------------------------------------------- #
def _candidate_urls(source: dict) -> list[str]:
    """Restituisce gli URL da provare. 'url' puo' essere stringa o lista.
    Gli URL placeholder (TODO_...) vengono ignorati."""
    raw = source.get("url") or source.get("urls")
    if raw is None:
        return []
    urls = raw if isinstance(raw, list) else [raw]
    return [u for u in urls if isinstance(u, str) and u and not u.startswith("TODO")]


def _http_get(url: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _parse_date(entry) -> Optional[str]:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.date(t.tm_year, t.tm_mon, t.tm_mday).isoformat()
            except Exception:
                pass
    return None


def _clean_text(html_or_text: str, limit: int = 600) -> str:
    if not html_or_text:
        return ""
    text = BeautifulSoup(html_or_text, "lxml").get_text(" ", strip=True)
    return text[:limit]


# --------------------------------------------------------------------------- #
#  Fetcher per tipo
# --------------------------------------------------------------------------- #
def _entry_text(entry) -> str:
    """Testo dell'item. Preferisce content:encoded (usato dalla Gazzetta
    Ufficiale e dai feed WordPress) e ripiega su summary/description."""
    content = entry.get("content")
    if content:
        try:
            val = content[0].get("value", "")
            if val:
                return val
        except Exception:
            pass
    return entry.get("summary", "")


def _items_from_feed(feed, source: dict) -> list[Item]:
    items = []
    for e in feed.entries[:MAX_ITEMS_PER_SOURCE]:
        items.append(Item(
            source_id=source["id"],
            source_name=source["name"],
            title=(e.get("title") or "").strip(),
            link=(e.get("link") or "").strip(),
            published=_parse_date(e),
            summary_raw=_clean_text(_entry_text(e)),
            item_type="rss",
            fetched_at=_now_iso(),
        ))
    return items


def _discover_feed_links(html_bytes: bytes, base_url: str) -> list[str]:
    """Cerca nell'HTML i tag <link> che dichiarano un feed RSS/Atom."""
    out: list[str] = []
    soup = BeautifulSoup(html_bytes, "lxml")
    for ln in soup.find_all("link"):
        ltype = (ln.get("type") or "").lower()
        href = ln.get("href")
        if href and ("rss" in ltype or "atom" in ltype):
            out.append(urljoin(base_url, href))
    return out


def fetch_rss(source: dict) -> tuple[list[Item], list[str]]:
    """Legge un feed RSS/Atom. Prova in ordine gli URL candidati e si ferma
    al primo che restituisce voci. Se un URL e' una pagina HTML (non un feed),
    cerca il feed dichiarato dentro la pagina (auto-rilevamento)."""
    notes: list[str] = []
    urls = _candidate_urls(source)
    if not urls:
        return [], ["nessun URL configurato"]

    for url in urls:
        # 1) prova diretta come feed
        try:
            if url.startswith(("http://", "https://")):
                resp = _http_get(url)
                content, final_url = resp.content, str(resp.url)
            else:
                feed = feedparser.parse(url)  # path locale (test)
                if feed.entries:
                    items = _items_from_feed(feed, source)
                    notes.append(f"{url} -> OK, {len(items)} voci")
                    return items, notes
                notes.append(f"{url} -> 0 voci")
                continue
        except Exception as ex:
            notes.append(f"{url} -> errore richiesta: {ex}")
            continue

        feed = feedparser.parse(content)
        if feed.entries:
            items = _items_from_feed(feed, source)
            notes.append(f"{url} -> OK, {len(items)} voci")
            return items, notes

        # 2) non e' un feed: prova ad auto-rilevare il feed dichiarato nell'HTML
        discovered = _discover_feed_links(content, final_url)
        for d in discovered:
            try:
                r2 = _http_get(d)
                feed2 = feedparser.parse(r2.content)
                if feed2.entries:
                    items = _items_from_feed(feed2, source)
                    notes.append(f"{url} -> 0 voci; AUTO-RILEVATO feed {d} -> OK, {len(items)} voci")
                    return items, notes
            except Exception as ex:
                notes.append(f"auto-rilevato {d} -> errore: {ex}")
        if discovered:
            notes.append(f"{url} -> 0 voci; feed dichiarati ma vuoti: {discovered}")
        else:
            notes.append(f"{url} -> 0 voci; nessun feed dichiarato nella pagina")

    return [], notes


def fetch_discovery(source: dict) -> tuple[list[Item], list[str]]:
    """Antenna di scoperta (dirittobancario): SOLO titolo + link, nessun testo.
    Se la fonte espone un RSS lo usa; il summary_raw resta sempre vuoto."""
    items, notes = fetch_rss(source)
    for it in items:
        it.summary_raw = ""        # niente testo della fonte protetta
        it.item_type = "discovery"
    return items, notes


def fetch_scrape(source: dict) -> tuple[list[Item], list[str]]:
    """Scraper generico per fonti senza feed.
    Se la fonte ha 'link_pattern' (espressione regolare), tiene SOLO i link il
    cui URL combacia col pattern (es. gli articoli con la data nell'indirizzo),
    scartando menu e navigazione. Senza pattern, ripiega su un'euristica grezza."""
    notes: list[str] = []
    items: list[Item] = []
    urls = _candidate_urls(source)
    if not urls:
        return [], ["nessun URL configurato"]

    pattern = source.get("link_pattern")
    rx = re.compile(pattern) if pattern else None
    seen: set[str] = set()

    for url in urls:
        try:
            resp = _http_get(url)
        except Exception as ex:
            notes.append(f"{url} -> errore richiesta: {ex}")
            continue
        soup = BeautifulSoup(resp.content, "lxml")
        main = soup.find("main") or soup.find("article") or soup.body or soup
        found = 0
        for a in main.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = urljoin(str(resp.url), a["href"])
            if rx:
                if not rx.search(href):
                    continue
            else:
                if len(text) < 12:        # euristica: scarta voci di menu corte
                    continue
            if href in seen:
                continue
            seen.add(href)
            # se il testo del link e' generico (es. "Vai"), usa l'attributo title
            title = text
            if len(text) < 8 and a.get("title"):
                title = a["title"].strip()
            items.append(Item(
                source_id=source["id"],
                source_name=source["name"],
                title=title or href,
                link=href,
                published=None,
                summary_raw="",
                item_type="scrape",
                fetched_at=_now_iso(),
            ))
            found += 1
            if found >= MAX_ITEMS_PER_SOURCE:
                break
        kind = "link (pattern)" if rx else "link grezzi (selettori da tarare)"
        notes.append(f"{url} -> {found} {kind}")
    return items, notes


def _matches(text: str, includes: list[str], excludes: list[str]) -> bool:
    t = text.lower()
    if excludes and any(x.lower() in t for x in excludes):
        return False
    if includes:
        return any(x.lower() in t for x in includes)
    return True


def fetch_rss_filtered(source: dict) -> tuple[list[Item], list[str]]:
    """Come fetch_rss, ma tiene solo le voci che combaciano con le parole chiave
    di materia (include_keywords / exclude_keywords). Per feed-firehose come la
    Gazzetta Ufficiale Serie Generale."""
    items, notes = fetch_rss(source)
    if not items:
        return items, notes
    inc = source.get("include_keywords") or []
    exc = source.get("exclude_keywords") or []
    kept = [it for it in items if _matches(f"{it.title} {it.summary_raw}", inc, exc)]
    notes.append(f"filtro materia: {len(kept)}/{len(items)} voci pertinenti")
    return kept, notes


def fetch_unconfigured(source: dict) -> tuple[list[Item], list[str]]:
    """Tipi non ancora implementati in fase 1 (es. eurlex)."""
    return [], [f"tipo '{source.get('type')}' da configurare in una fase successiva"]


# Mappa: tipo fonte -> funzione
FETCHERS = {
    "rss": fetch_rss,
    "rss_filtered": fetch_rss_filtered,
    "discovery": fetch_discovery,
    "scrape": fetch_scrape,
    "eurlex_anchor": fetch_unconfigured,
    "link_target_only": lambda s: ([], ["solo destinazione link, non interrogata"]),
    "email_alert": fetch_unconfigured,
}


# --------------------------------------------------------------------------- #
#  Orchestratore
# --------------------------------------------------------------------------- #
def load_sources() -> list[dict]:
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("sources", [])


def run() -> int:
    if not SOURCES_FILE.exists():
        print(f"ERRORE: {SOURCES_FILE} non trovato. "
              f"Deve stare nella stessa cartella di collect.py.")
        return 1

    DATA_DIR.mkdir(exist_ok=True)
    sources = load_sources()

    all_items: list[Item] = []
    report: list[tuple[str, str, str]] = []   # (stato, id, dettaglio)

    for src in sources:
        sid = src.get("id", "?")
        if src.get("enabled") is False:
            report.append(("SKIP", sid, "disabilitata in config"))
            continue
        fetcher = FETCHERS.get(src.get("type"), fetch_unconfigured)
        try:
            items, notes = fetcher(src)
        except Exception as ex:
            report.append(("ERR", sid, f"eccezione: {ex}"))
            continue

        detail = " | ".join(notes)
        if items:
            all_items.extend(items)
            report.append(("OK", sid, detail))
        elif any("da configurare" in n or "solo destinazione" in n for n in notes):
            report.append(("SKIP", sid, detail))
        elif any("nessun URL" in n for n in notes):
            report.append(("ERR", sid, detail))
        else:
            report.append(("VUOTO", sid, detail))

    # Salvataggio
    out_path = DATA_DIR / f"collected_{_today()}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([asdict(i) | {"uid": i.uid()} for i in all_items],
                  f, ensure_ascii=False, indent=2)

    # Riepilogo (pensato per essere copiato e incollato)
    print("\n" + "=" * 60)
    print(f"  RIEPILOGO RACCOLTA  {_today()}")
    print("=" * 60)
    width = max((len(r[1]) for r in report), default=10)
    for stato, sid, detail in report:
        print(f"[{stato:<5}] {sid:<{width}}  {detail}")
    print("-" * 60)
    print(f"  Voci totali raccolte: {len(all_items)}")
    print(f"  File salvato: {out_path.relative_to(ROOT)}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
