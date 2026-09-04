#!/usr/bin/env python3
"""
Funzioni condivise tra main.py (monitoraggio ogni 5 minuti) e report.py
(report giornaliero/settimanale di autovalutazione).

Tenerle in un unico posto evita di duplicare la logica (e i bug) tra i due
script.
"""

import os
import json
import requests
import yaml
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
LOG_PATH = os.path.join(BASE_DIR, "data", "alerts_log.jsonl")

# Orario di borsa USA (NYSE/Nasdaq), usato per "pesare" correttamente il
# volume a metà giornata invece di confrontarlo con una media di giorni interi.
NY_TZ = ZoneInfo("America/New_York")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def trading_day_fraction_elapsed() -> float:
    """Stima quanta parte della sessione di borsa USA (9:30-16:00 ora di New
    York) è trascorsa in questo momento. Serve a correggere il confronto sul
    volume: a metà mattina è normale che il volume "di oggi" sia solo una
    frazione del volume medio di una giornata intera.

    NOTA: è una stima semplice, non tiene conto delle festività di borsa
    (es. Thanksgiving), solo di weekend e orari di apertura/chiusura.
    """
    now_ny = datetime.now(timezone.utc).astimezone(NY_TZ)

    if now_ny.weekday() >= 5:  # sabato o domenica: mercato chiuso
        return 1.0

    open_dt = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)

    if now_ny <= open_dt:
        return 0.03  # pre-mercato: soglia minima per evitare rapporti assurdi
    if now_ny >= close_dt:
        return 1.0

    total = (close_dt - open_dt).total_seconds()
    elapsed = (now_ny - open_dt).total_seconds()
    return max(elapsed / total, 0.03)


def send_telegram_message(text: str):
    """Invia un messaggio al bot Telegram configurato tramite variabili d'ambiente."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ATTENZIONE] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti: messaggio non inviato.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERRORE] Invio Telegram fallito: {e}")


def load_log() -> list:
    """Carica lo storico delle previsioni da data/alerts_log.jsonl.
    Se il file non esiste ancora (prima esecuzione) restituisce una lista vuota.
    Righe corrotte vengono ignorate singolarmente, senza bloccare la lettura."""
    if not os.path.exists(LOG_PATH):
        return []
    entries = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def save_log(entries: list, max_age_days: int = 120):
    """Salva lo storico in formato JSON Lines (un oggetto per riga).

    Pulizia automatica: le previsioni già risolte da più di max_age_days
    vengono scartate per non far crescere il file all'infinito. Le
    previsioni NON ancora risolte vengono sempre mantenute, indipendentemente
    dall'età, finché non vengono risolte."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    now = datetime.now(timezone.utc)
    pruned = []
    for e in entries:
        if not e.get("resolved"):
            pruned.append(e)
            continue
        try:
            resolved_at = datetime.fromisoformat(e["resolved_at"])
            if now - resolved_at <= timedelta(days=max_age_days):
                pruned.append(e)
        except Exception:
            pruned.append(e)  # in dubbio, la teniamo piuttosto che perderla

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        for e in pruned:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
