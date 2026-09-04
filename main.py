#!/usr/bin/env python3
"""
Strumento di monitoraggio azioni con checklist di valutazione.

Ogni esecuzione (pensata per girare ogni 5 minuti via GitHub Actions):
  1. Scarica dati di prezzo/volume per i ticker configurati (yfinance)
  2. Calcola una checklist di segnali: variazione prezzo, volume anomalo,
     incrocio medie mobili, notizie rilevanti recenti (RSS)
  3. Se il punteggio totale supera la soglia, invia un messaggio Telegram

NOTA IMPORTANTE: questo strumento genera segnali puramente informativi
basati su regole semplici. NON è consulenza finanziaria e non garantisce
nulla sui movimenti di mercato. Le decisioni di acquisto/vendita restano
sempre e solo tue.
"""

import os
import sys
import yaml
import requests
import feedparser
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")

# Lista di riserva se il download da Wikipedia dei componenti S&P 500 fallisse
# (sottoinsieme di grandi titoli USA, aggiornabile a mano quando serve)
SP500_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "AVGO",
    "TSLA", "LLY", "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "NFLX",
    "JNJ", "BAC", "CRM", "ABBV", "MRK", "ORCL", "CVX", "KO", "AMD", "PEP",
    "ADBE", "WMT", "TMO", "MCD", "CSCO", "ACN", "LIN", "ABT", "IBM", "GE",
    "DHR", "PM", "TXN", "INTU", "QCOM", "CAT", "VZ", "AXP", "AMGN", "NOW",
]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_sp500_universe() -> list:
    """Recupera l'elenco dei ticker S&P 500 da Wikipedia; usa una lista di
    riserva statica se il download fallisce (rete assente, pagina cambiata, ecc.)."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        symbols = tables[0]["Symbol"].tolist()
        # Yahoo Finance usa "-" al posto di "." per alcuni ticker (es. BRK.B -> BRK-B)
        symbols = [s.replace(".", "-") for s in symbols]
        if len(symbols) > 50:
            return symbols
    except Exception as e:
        print(f"[ATTENZIONE] Impossibile scaricare la lista S&P 500 da Wikipedia: {e}")

    print("[INFO] Uso la lista di riserva statica per lo scouting.")
    return SP500_FALLBACK


def scout_top_movers(cfg: dict) -> list:
    """Scarica dati giornalieri in blocco per l'universo configurato e
    individua i titoli con variazione % o volume più anomali (top mover)."""
    scouting_cfg = cfg.get("scouting", {})
    if not scouting_cfg.get("enabled"):
        return []

    universe_name = scouting_cfg.get("universe", "sp500")
    if universe_name != "sp500":
        print(f"[ATTENZIONE] Universo di scouting '{universe_name}' non supportato, salto lo scouting.")
        return []

    universe = get_sp500_universe()
    print(f"[INFO] Scouting su {len(universe)} titoli dell'universo {universe_name}...")

    try:
        # Un'unica chiamata "bulk" per tutti i titoli: molto più leggera di
        # 500 chiamate singole e riduce il rischio di rate-limit.
        data = yf.download(
            tickers=universe,
            period="25d",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except Exception as e:
        print(f"[ERRORE] Download bulk per lo scouting fallito: {e}")
        return []

    candidates = []
    for ticker in universe:
        try:
            df = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
            df = df.dropna()
            if len(df) < 2:
                continue

            last_close = float(df["Close"].iloc[-1])
            prev_close = float(df["Close"].iloc[-2])
            change_pct = (last_close - prev_close) / prev_close * 100

            avg_volume = df["Volume"].iloc[:-1].mean()
            last_volume = float(df["Volume"].iloc[-1])
            volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0

            passes_change = abs(change_pct) >= scouting_cfg.get("min_abs_change_pct", 2.0)
            passes_volume = volume_ratio >= scouting_cfg.get("min_volume_ratio", 1.5)

            if passes_change or passes_volume:
                # Punteggio di "interesse" grezzo per ordinare i candidati
                interest_score = abs(change_pct) + volume_ratio
                candidates.append((ticker, interest_score, change_pct, volume_ratio))
        except Exception:
            continue  # ticker con dati mancanti/incompleti: si salta senza bloccare tutto

    candidates.sort(key=lambda x: x[1], reverse=True)
    top_n = scouting_cfg.get("top_n", 10)
    top_tickers = [c[0] for c in candidates[:top_n]]

    for t, score, chg, vol in candidates[:top_n]:
        print(f"  [SCOUT] {t}: var {chg:+.2f}%, volume {vol:.1f}x media -> interesse {score:.2f}")

    return top_tickers


def send_telegram_message(text: str):
    """Invia un messaggio al bot Telegram configurato tramite variabili d'ambiente."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ATTENZIONE] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti: alert non inviato.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERRORE] Invio Telegram fallito: {e}")


def analyze_price_and_volume(ticker: str, cfg: dict) -> dict:
    """Scarica dati intraday e giornalieri e calcola i segnali quantitativi."""
    result = {
        "ticker": ticker,
        "score": 0,
        "reasons": [],
        "last_price": None,
    }

    try:
        tk = yf.Ticker(ticker)

        # Dati intraday (ultimi 2 giorni, intervallo 5 minuti) per variazione a breve termine
        intraday = tk.history(period="2d", interval="5m")
        # Dati giornalieri (ultimi ~60 giorni) per medie mobili e volume medio
        daily = tk.history(period="60d", interval="1d")

        if intraday.empty or daily.empty:
            result["reasons"].append("Dati insufficienti da Yahoo Finance.")
            return result

        last_price = float(intraday["Close"].iloc[-1])
        result["last_price"] = round(last_price, 2)

        # --- Fattore 1: variazione prezzo ultimi 5 minuti ---
        if len(intraday) >= 2:
            prev_price = float(intraday["Close"].iloc[-2])
            change_5m_pct = (last_price - prev_price) / prev_price * 100
            if abs(change_5m_pct) >= cfg["thresholds"]["price_change_5m_pct"]:
                result["score"] += 1
                direzione = "rialzo" if change_5m_pct > 0 else "ribasso"
                result["reasons"].append(
                    f"Movimento rapido: {change_5m_pct:+.2f}% negli ultimi 5 min ({direzione})."
                )

        # --- Fattore 2: variazione prezzo giornaliera ---
        today_open = float(daily["Open"].iloc[-1])
        change_1d_pct = (last_price - today_open) / today_open * 100
        if abs(change_1d_pct) >= cfg["thresholds"]["price_change_1d_pct"]:
            result["score"] += 1
            direzione = "rialzo" if change_1d_pct > 0 else "ribasso"
            result["reasons"].append(
                f"Variazione giornaliera: {change_1d_pct:+.2f}% ({direzione})."
            )

        # --- Fattore 3: volume anomalo ---
        avg_volume = daily["Volume"].iloc[:-1].mean()
        today_volume = daily["Volume"].iloc[-1]
        if avg_volume > 0:
            volume_ratio = today_volume / avg_volume
            if volume_ratio >= cfg["thresholds"]["volume_spike_ratio"]:
                result["score"] += 1
                result["reasons"].append(
                    f"Volume anomalo: {volume_ratio:.1f}x rispetto alla media storica."
                )

        # --- Fattore 4: incrocio medie mobili (SMA breve vs lunga) ---
        short_p = cfg["thresholds"]["sma_short"]
        long_p = cfg["thresholds"]["sma_long"]
        if len(daily) >= long_p + 1:
            sma_short_series = daily["Close"].rolling(short_p).mean()
            sma_long_series = daily["Close"].rolling(long_p).mean()

            sma_short_today, sma_long_today = sma_short_series.iloc[-1], sma_long_series.iloc[-1]
            sma_short_yday, sma_long_yday = sma_short_series.iloc[-2], sma_long_series.iloc[-2]

            crossed_up = sma_short_yday <= sma_long_yday and sma_short_today > sma_long_today
            crossed_down = sma_short_yday >= sma_long_yday and sma_short_today < sma_long_today

            if crossed_up:
                result["score"] += 1
                result["reasons"].append(
                    f"Incrocio rialzista: media mobile {short_p}gg ha superato quella a {long_p}gg."
                )
            elif crossed_down:
                result["score"] += 1
                result["reasons"].append(
                    f"Incrocio ribassista: media mobile {short_p}gg è scesa sotto quella a {long_p}gg."
                )

    except Exception as e:
        result["reasons"].append(f"Errore durante l'analisi quantitativa: {e}")

    return result


def check_relevant_news(ticker: str, cfg: dict) -> dict:
    """Controlla i feed RSS configurati per notizie recenti con parole chiave rilevanti."""
    result = {"score": 0, "reasons": [], "matched_titles": []}

    lookback = timedelta(hours=cfg["thresholds"]["news_lookback_hours"])
    now = datetime.now(timezone.utc)
    keywords = [k.lower() for k in cfg["news_keywords"]]

    # Nome "semplice" del titolo per il matching testuale (rimuove suffissi tipo .MI)
    ticker_name = ticker.split(".")[0].lower()

    matches = []
    for feed_info in cfg["rss_feeds"]:
        try:
            feed = feedparser.parse(feed_info["url"])
        except Exception as e:
            result["reasons"].append(f"Impossibile leggere il feed {feed_info['name']}: {e}")
            continue

        for entry in feed.entries:
            title = getattr(entry, "title", "") or ""
            title_lower = title.lower()

            # Filtro temporale, se il feed fornisce la data di pubblicazione
            published = getattr(entry, "published_parsed", None)
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if now - pub_dt > lookback:
                    continue

            keyword_hit = any(k in title_lower for k in keywords)
            ticker_hit = ticker_name in title_lower

            if keyword_hit or ticker_hit:
                matches.append(f"[{feed_info['name']}] {title}")

    if len(matches) >= cfg["thresholds"]["news_min_count"]:
        result["score"] += 1
        result["reasons"].append(
            f"Trovate {len(matches)} notizie potenzialmente rilevanti nelle ultime "
            f"{cfg['thresholds']['news_lookback_hours']} ore."
        )
        result["matched_titles"] = matches[:5]  # limitiamo per non allungare troppo il messaggio

    return result


def build_alert_message(ticker: str, quant: dict, news: dict, source: str) -> str:
    total_score = quant["score"] + news["score"]
    label = "📌 Watchlist" if source == "watchlist" else "🔍 Scouting"
    lines = [f"<b>⚠️ Segnale su {ticker}</b> ({label})"]
    if quant.get("last_price") is not None:
        lines.append(f"Prezzo attuale: {quant['last_price']}")
    lines.append(f"Punteggio checklist: {total_score}")
    lines.append("")
    for r in quant["reasons"] + news["reasons"]:
        lines.append(f"• {r}")
    if news["matched_titles"]:
        lines.append("")
        lines.append("Notizie correlate:")
        for t in news["matched_titles"]:
            lines.append(f"  - {t}")
    lines.append("")
    lines.append("ℹ️ Segnale informativo generato da regole automatiche, NON è consulenza finanziaria.")
    return "\n".join(lines)


def main():
    cfg = load_config()
    threshold = cfg["thresholds"]["alert_score_threshold"]

    watchlist = list(cfg.get("tickers", []))
    scouted = scout_top_movers(cfg)

    # Evita di analizzare due volte un titolo se è sia in watchlist sia scovato ora
    scouted_unique = [t for t in scouted if t not in watchlist]

    all_targets = [(t, "watchlist") for t in watchlist] + [(t, "scouting") for t in scouted_unique]

    any_alert = False
    for ticker, source in all_targets:
        print(f"--- Analisi {ticker} ({source}) ---")
        quant = analyze_price_and_volume(ticker, cfg)
        news = check_relevant_news(ticker, cfg)
        total_score = quant["score"] + news["score"]
        print(f"Punteggio totale: {total_score} (soglia: {threshold})")

        if total_score >= threshold:
            any_alert = True
            message = build_alert_message(ticker, quant, news, source)
            send_telegram_message(message)
        else:
            print("Nessun segnale rilevante al momento.")

    if not any_alert:
        print("Ciclo completato: nessun alert da inviare.")


if __name__ == "__main__":
    main()
