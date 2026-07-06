#!/usr/bin/env python3
"""
Watcher CROUS Bourges — envoie une alerte Telegram dès qu'un logement
apparaît sur trouverunlogement.lescrous.fr pour Bourges, avec une alerte
prioritaire si c'est la résidence Hôtel Dieu.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# --- Configuration ---------------------------------------------------

SEARCH_URLS = [
    "https://trouverunlogement.lescrous.fr/tools/45/search?bounds=2.30_47.14_2.50_47.02",
]

MAX_PAGES_PER_SEARCH = 6

PRIORITY_KEYWORDS = ["HOTEL DIEU", "HÔTEL DIEU", "HOTEL-DIEU"]

STATE_FILE = Path(__file__).parent / "state" / "seen.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crous-bourges-watcher/1.0; usage personnel non-commercial)"
}

ACCOMMODATION_RE = re.compile(r"/tools/(\d+)/accommodations/(\d+)")


def fetch_listings(base_url: str) -> dict[str, dict]:
    listings: dict[str, dict] = {}

    for page in range(1, MAX_PAGES_PER_SEARCH + 1):
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page}"

        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        links = [a for a in soup.find_all("a", href=True) if ACCOMMODATION_RE.search(a["href"])]
        if not links:
            break

        for a in links:
            m = ACCOMMODATION_RE.search(a["href"])
            acc_id = m.group(2)
            if acc_id in listings:
                continue

            card = a.find_parent(["li", "article", "div"]) or a
            text = card.get_text(separator=" | ", strip=True)

            href = a["href"]
            full_url = f"https://trouverunlogement.lescrous.fr{href}" if href.startswith("/") else href

            listings[acc_id] = {"url": full_url, "text": text[:300]}

        time.sleep(1)

    return listings


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants, message non envoyé :")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=20,
    )
    resp.raise_for_status()


def main() -> None:
    first_run = not STATE_FILE.exists()
    seen = load_seen()

    all_current: dict[str, dict] = {}
    for search_url in SEARCH_URLS:
        try:
            all_current.update(fetch_listings(search_url))
        except requests.RequestException as e:
            print(f"Erreur réseau sur {search_url}: {e}", file=sys.stderr)

    if first_run:
        save_seen(set(all_current.keys()))
        print(f"Premier lancement : {len(all_current)} logement(s) déjà en ligne, initialisés sans alerte.")
        return

    new_ids = [acc_id for acc_id in all_current if acc_id not in seen]

    if new_ids:
        for acc_id in new_ids:
            info = all_current[acc_id]
            is_priority = any(k.lower() in info["text"].lower() for k in PRIORITY_KEYWORDS)
            prefix = "🔥 HÔTEL DIEU DISPONIBLE !" if is_priority else "🏠 Nouveau logement CROUS Bourges"
            message = f"{prefix}\n{info['text']}\n{info['url']}"
            send_telegram(message)
            print(message)
    else:
        print("Aucun nouveau logement pour le moment.")

    save_seen(set(all_current.keys()) | seen)


if __name__ == "__main__":
    main()
