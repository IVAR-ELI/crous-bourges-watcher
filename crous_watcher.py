#!/usr/bin/env python3
"""
Watcher CROUS Bourges — surveille trouverunlogement.lescrous.fr pour Bourges
et envoie une alerte Telegram dès qu'un logement apparaît, avec une alerte
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

# --- Configuration (valeurs pour Bourges) ----------------------------
TOOL_ID = 47  # numéro de campagne, visible dans l'URL /tools/XX/search

# bounds = ouest_nord_est_sud  (longitude_latitude_longitude_latitude)
WEST, NORTH, EAST, SOUTH = 2.3239701, 47.1300959, 2.4719573, 47.0259507

SEARCH_PAGE_URL = (
    f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/search"
    f"?bounds={WEST}_{NORTH}_{EAST}_{SOUTH}"
)
API_URL = f"https://trouverunlogement.lescrous.fr/api/fr/search/{TOOL_ID}"

PRIORITY_KEYWORDS = ["hotel dieu", "hôtel dieu", "hotel-dieu"]

STATE_FILE = Path(__file__).parent / "state" / "seen.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": SEARCH_PAGE_URL,
}

ACCOMMODATION_RE = re.compile(r"/tools/\d+/accommodations/(\d+)")


def _acc_url(acc_id: str) -> str:
    return f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/accommodations/{acc_id}"


def _walk_find_items(obj):
    """Cherche récursivement une liste d'items d'hébergement dans le JSON."""
    found = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in ("items", "results", "data") and isinstance(val, list):
                if val and isinstance(val[0], dict) and "id" in val[0]:
                    found.extend(val)
            found.extend(_walk_find_items(val))
    elif isinstance(obj, list):
        for el in obj:
            found.extend(_walk_find_items(el))
    return found


def fetch_via_api() -> dict:
    """Essaie l'API JSON interne. Renvoie {} si le format ne correspond pas."""
    payload = {
        "idTool": TOOL_ID,
        "need_aggregation": False,
        "page": 1,
        "pageSize": 100,
        "sector": None,
        "occupationModes": [],
        "location": [
            {"lon": WEST, "lat": NORTH},
            {"lon": EAST, "lat": SOUTH},
        ],
        "residence": None,
        "precision": 8,
        "equipments": [],
        "price": {"min": 0, "max": 100000},
    }

    resp = requests.post(API_URL, headers=BASE_HEADERS, json=payload, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    raw_items = _walk_find_items(data)

    listings = {}
    for it in raw_items:
        acc_id = str(it.get("id"))
        if not acc_id or acc_id == "None":
            continue
        label = it.get("label") or it.get("name") or ""
        residence = it.get("residence") or {}
        res_label = ""
        city = ""
        if isinstance(residence, dict):
            res_label = residence.get("label") or residence.get("name") or ""
            addr = residence.get("address") or {}
            if isinstance(addr, dict):
                city = addr.get("city") or ""
        text = " — ".join(x for x in [label, res_label, city] if x)
        listings[acc_id] = {"url": _acc_url(acc_id), "text": text or f"logement {acc_id}"}

    return listings


def fetch_via_html() -> dict:
    """Repli : lit le HTML de la page de recherche (au cas où l'API échoue)."""
    from bs4 import BeautifulSoup

    listings = {}
    for page in range(1, 7):
        url = f"{SEARCH_PAGE_URL}&page={page}"
        resp = requests.get(url, headers={"User-Agent": BASE_HEADERS["User-Agent"]}, timeout=20)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        anchors = [a for a in soup.find_all("a", href=True) if ACCOMMODATION_RE.search(a["href"])]
        if not anchors:
            break
        for a in anchors:
            acc_id = ACCOMMODATION_RE.search(a["href"]).group(1)
            if acc_id in listings:
                continue
            card = a.find_parent(["li", "article", "div"]) or a
            listings[acc_id] = {"url": _acc_url(acc_id), "text": card.get_text(" | ", strip=True)[:300]}
        time.sleep(1)
    return listings


def fetch_listings() -> dict:
    try:
        api = fetch_via_api()
        if api:
            print(f"[API] {len(api)} logement(s).")
            return api
        print("[API] réponse vide, tentative HTML…")
    except Exception as e:
        print(f"[API] échec ({e}), tentative HTML…")

    html = fetch_via_html()
    print(f"[HTML] {len(html)} logement(s).")
    return html


def load_seen() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants :")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=20)
    resp.raise_for_status()


def main() -> None:
    first_run = not STATE_FILE.exists()
    seen = load_seen()

    current = fetch_listings()

    if not current and not first_run:
        print("Aucun logement récupéré (API + HTML vides).", file=sys.stderr)

    if first_run:
        save_seen(set(current.keys()))
        print(f"Premier lancement : {len(current)} logement(s) initialisé(s) sans alerte.")
        return

    new_ids = [i for i in current if i not in seen]

    if new_ids:
        for acc_id in new_ids:
            info = current[acc_id]
            is_priority = any(k in info["text"].lower() for k in PRIORITY_KEYWORDS)
            prefix = "🔥 HÔTEL DIEU DISPONIBLE !" if is_priority else "🏠 Nouveau logement CROUS Bourges"
            send_telegram(f"{prefix}\n{info['text']}\n{info['url']}")
            print("ALERTE:", info["text"], info["url"])
    else:
        print("Aucun nouveau logement pour le moment.")

    save_seen(set(current.keys()) | seen)


if __name__ == "__main__":
    main()
