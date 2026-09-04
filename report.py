#!/usr/bin/env python3
"""
Report di autovalutazione: calcola quante previsioni passate si sono
rivelate corrette (direzione prevista vs prezzo effettivo dopo l'orizzonte
configurato) e invia un riepilogo su Telegram.

Pensato per girare una volta al giorno (workflow separato da main.py, che
gira ogni 5 minuti). Legge solo lo storico già salvato da main.py: non
serve accesso a Yahoo Finance, solo a Telegram.
"""

from datetime import datetime, timedelta, timezone

from common import load_log, send_telegram_message


def compute_stats(entries: list, window: timedelta, now: datetime) -> dict:
    """Calcola le statistiche sulle previsioni RISOLTE entro la finestra
    temporale indicata (es. ultime 24 ore, ultimi 7 giorni)."""
    resolved_in_window = []
    for e in entries:
        if not e.get("resolved") or not e.get("resolved_at"):
            continue
        try:
            resolved_at = datetime.fromisoformat(e["resolved_at"])
        except Exception:
            continue
        if now - resolved_at <= window:
            resolved_in_window.append(e)

    classificabili = [e for e in resolved_in_window if e.get("outcome") in ("corretto", "errato")]
    corrette = [e for e in classificabili if e["outcome"] == "corretto"]

    return {
        "totale_risolte": len(resolved_in_window),
        "classificabili": len(classificabili),
        "corrette": len(corrette),
        "percentuale": (len(corrette) / len(classificabili) * 100) if classificabili else None,
    }


def format_stats_block(title: str, stats: dict) -> list:
    lines = [f"<b>{title}</b>"]
    if stats["classificabili"] == 0:
        lines.append("Nessuna previsione con direzione chiara risolta in questo periodo.")
    else:
        lines.append(f"Previsioni valutate: {stats['classificabili']}")
        lines.append(f"Corrette: {stats['corrette']} ({stats['percentuale']:.0f}%)")

    non_classificabili = stats["totale_risolte"] - stats["classificabili"]
    if non_classificabili > 0:
        lines.append(f"(+{non_classificabili} segnali 'incerti', esclusi dal calcolo)")

    return lines


def main():
    entries = load_log()
    now = datetime.now(timezone.utc)

    if not entries:
        send_telegram_message(
            "<b>📊 Report di autovalutazione</b>\n\n"
            "Non ci sono ancora dati sufficienti: il monitoraggio non ha "
            "ancora registrato o risolto nessuna previsione."
        )
        print("Nessun dato in archivio, inviato messaggio informativo.")
        return

    daily_stats = compute_stats(entries, timedelta(hours=24), now)
    weekly_stats = compute_stats(entries, timedelta(days=7), now)

    lines = ["<b>📊 Report di autovalutazione</b>", ""]
    lines += format_stats_block("Ultime 24 ore", daily_stats)
    lines.append("")
    lines += format_stats_block("Ultimi 7 giorni", weekly_stats)
    lines.append("")
    lines.append(
        "ℹ️ \"Corretto\" = il prezzo si è mosso nella direzione indicata "
        "dall'alert (rialzo/ribasso) entro 24 ore, indipendentemente "
        "dall'entità del movimento. Non misura guadagno o perdita "
        "potenziale, solo la direzione. I segnali con lettura 'incerta' "
        "non vengono conteggiati come corretti o errati."
    )

    send_telegram_message("\n".join(lines))
    print("Report inviato.")
    print(f"Statistiche 24h: {daily_stats}")
    print(f"Statistiche 7g: {weekly_stats}")


if __name__ == "__main__":
    main()
