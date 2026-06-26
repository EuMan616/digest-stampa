#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIGEST STAMPA - ELABORAZIONE
============================
Legge l'ultimo collected_<data>.json, tiene solo le notizie NON gia' viste ed
entro la finestra temporale, mette un tetto per testata (le N piu' recenti),
elimina i doppioni e divide in due regioni: ita ed estero.
Salva data/digest_<data>.json con la struttura {ita: [...], estero: [...]}.

Si lancia dopo collect.py:  python process.py
"""

from __future__ import annotations

import os
import sys
import re
import json
import glob
import datetime
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "state"
SEEN_FILE = STATE_DIR / "seen.json"

WINDOW_DAYS = 2        # solo notizie di ieri/oggi
PER_SOURCE_CAP = 5     # massimo notizie per testata al giorno (taratura: abbassa se troppo)


def _today() -> str:
    return datetime.date.today().isoformat()


def _latest_collected() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "collected_*.json")))
    return Path(files[-1]) if files else None


def _load_seen() -> tuple[set[str], bool]:
    if os.environ.get("RESET_STATE", "").strip().lower() in ("1", "true", "yes"):
        return set(), True
    if not SEEN_FILE.exists():
        return set(), True
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen", [])), False
    except Exception:
        return set(), False


def _within_window(published: str | None) -> bool:
    if not published:
        return False
    try:
        d = datetime.date.fromisoformat(published)
    except Exception:
        return False
    return (datetime.date.today() - d).days <= WINDOW_DAYS


def _norm_title(title: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()[:80]


def select_new(items: list[dict], seen: set[str], first_run: bool) -> list[dict]:
    out = []
    for it in items:
        if it.get("uid") in seen:
            continue
        if it.get("published"):
            if _within_window(it["published"]):
                out.append(it)
        elif not first_run:
            out.append(it)
    return out


def cap_per_source(items: list[dict], n: int) -> list[dict]:
    by_src: dict[str, list] = defaultdict(list)
    for it in items:
        by_src[it.get("source_id", "?")].append(it)
    out = []
    for lst in by_src.values():
        lst.sort(key=lambda x: x.get("published") or "", reverse=True)
        out.extend(lst[:n])
    return out


def deduplicate(items: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for it in items:
        key = _norm_title(it.get("title", "")) or it.get("uid")
        if key not in groups:
            it = dict(it)
            it["also_in"] = []
            groups[key] = it
        else:
            src = it.get("source_name", "")
            if src and src != groups[key].get("source_name") and src not in groups[key]["also_in"]:
                groups[key]["also_in"].append(src)
    return list(groups.values())


def run() -> int:
    collected = _latest_collected()
    if not collected:
        print("ERRORE: nessun collected_*.json. Esegui prima collect.py.")
        return 1

    items = json.loads(collected.read_text(encoding="utf-8"))
    seen, first_run = _load_seen()

    new_items = select_new(items, seen, first_run)
    new_items = cap_per_source(new_items, PER_SOURCE_CAP)

    ita = deduplicate([it for it in new_items if it.get("regione") == "ita"])
    estero = deduplicate([it for it in new_items if it.get("regione") == "estero"])
    for bucket in (ita, estero):
        bucket.sort(key=lambda x: x.get("published") or "", reverse=True)

    DATA_DIR.mkdir(exist_ok=True)
    digest = {"generated_at": _today(), "ita": ita, "estero": estero}
    digest_path = DATA_DIR / f"digest_{_today()}.json"
    digest_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    STATE_DIR.mkdir(exist_ok=True)
    updated = seen | {it["uid"] for it in items if it.get("uid")}
    SEEN_FILE.write_text(json.dumps({"seen": sorted(updated), "updated_at": _today()},
                                    ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"  ELABORAZIONE STAMPA  {_today()}")
    print("=" * 60)
    print(f"  Voci grezze in ingresso:  {len(items)}")
    print(f"  Prima esecuzione:         {'si' if first_run else 'no'}")
    print(f"  Notizie ITALIA:           {len(ita)}")
    print(f"  Notizie ESTERO:           {len(estero)}")
    print(f"  Tetto per testata:        {PER_SOURCE_CAP}")
    print(f"  Digest: {digest_path.relative_to(ROOT)}")
    print("=" * 60 + "\n")
    for label, bucket in (("ITALIA", ita), ("ESTERO", estero)):
        if bucket:
            print(f"{label}:")
            for it in bucket[:8]:
                print(f"  - ({it.get('source_name','')}) {it.get('title','')[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
