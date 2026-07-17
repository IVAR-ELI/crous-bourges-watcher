#!/usr/bin/env python3
"""
Watcher CROUS Bourges — surveille trouverunlogement.lescrous.fr pour Bourges
et envoie une alerte Telegram dès qu'un logement apparaît, avec une alerte
prioritaire si c'est la résidence Hôtel Dieu.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

# --- Configuration (valeurs pour Bourges) ----------------------------
TOOL_ID = 47
WEST, NORTH, EAST, SOUTH = -5.0, 51.5, 9.5, 41.0

API_URL = f"https://trouverunlogement.lescrous.fr/api/fr/search/{TOOL_ID}"

PRIORITY_KEYWORDS = ["hotel dieu", "hôtel dieu", "hotel-dieu"]

STATE_FILE = Path(__file__).parent / "state" / "seen.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "Accept": "application/ld+json, application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/search",
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
}

# Payload calé exactement sur le workflow n8n qui fonctionne
PAYLOAD = {
    "idTool": TOOL_ID,
    "need_aggregation": True,
    "page": 1,
    "pageSize": 24,
    "sector": None,
    "occupationModes": [],
    "location": [
        {"lon": WEST, "lat": NORTH},
        {"lon": EAST, "lat": SOUTH},
    ],
    "residence": None,
    "precision": 6,
    "equipment": [],
    "price": {"max": 10000000},
    "area": {"min": 0},
    "adaptedPmr": False,
    "toolMechanism": "residual",
}


def _acc_url(acc_id: str) -> str:
    return f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/accommodations/{acc_id}"


def fetch_listings() -> dict:
    """Interroge l'API et retourne {id: infos}."""
    resp = requests.post(API_URL, headers=HEADERS, json=PAYLOAD, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, dict) or "results" not in data or "items" not in data.get("results", {}):
        print("Réponse inattendue de l'API (pas de results.items).", file=sys.stderr)
        return {}

    items = data["results"]["items"]

    listings = {}
    for l in items:
        acc_id = str(l.get("id"))
        if not acc_id or acc_id == "None":
            continue

        nom = ""
        adresse = ""
        residence = l.get("residence") or {}
        if isinstance(residence, dict):
            nom = residence.get("label") or ""
            adresse = residence.get("address") or ""

        type_log = l.get("label") or ""

        prix = ""
        modes = l.get("occupationModes") or []
        if modes and isinstance(modes[0], dict):
            rent = modes[0].get("rent") or {}
            if isinstance(rent, dict) and rent.get("min") is not None:
                prix = f"{rent['min'] / 100:.0f} €"

        text = " — ".join(x for x in [nom, type_log, adresse, prix] if x)
        listings[acc_id] = {
            "url": _acc_url(acc_id),
            "text": text or f"logement {acc_id}",
            "nom": nom,
            "type": type_log,
            "adresse": adresse,
            "prix": prix,
        }

    return listings


def load_seen() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants :")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=20,
    )
    resp.raise_for_status()


def main() -> None:
    first_run = not STATE_FILE.exists()
    seen = load_seen()

    try:
        current = fetch_listings()
    except requests.RequestException as e:
        print(f"Erreur réseau/API : {e}", file=sys.stderr)
        sys.exit(1)

    print(f"{len(current)} logement(s) trouvé(s) pour la zone.")

    #if first_run:
     #   save_seen(set(current.keys()))
    #  print(f"Premier lancement : {len(current)} logement(s) initialisé(s) sans alerte.")
     #   return

    new_ids = [i for i in current if i not in seen]

    if new_ids:
        for acc_id in new_ids:
            info = current[acc_id]
            is_priority = any(k in info["text"].lower() for k in PRIORITY_KEYWORDS)
            entete = "🔥 *HÔTEL DIEU DISPONIBLE !*" if is_priority else "🏠 *Nouveau logement CROUS Bourges*"
            msg = (
                f"{entete}\n\n"
                f"🏢 Résidence : {info['nom'] or 'N/A'}\n"
                f"📏 Type : {info['type'] or 'N/A'}\n"
                f"📍 Adresse : {info['adresse'] or 'N/A'}\n"
                f"💰 Prix : {info['prix'] or 'N/A'}\n\n"
                f"👉 {info['url']}"
            )
            send_telegram(msg)
            print("ALERTE:", info["text"])
    else:
        print("Aucun nouveau logement pour le moment.")

    save_seen(set(current.keys()) | seen)


if __name__ == "__main__":
    main()
