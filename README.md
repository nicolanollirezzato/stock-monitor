# Monitoraggio Azioni con Alert Telegram

Strumento automatico che controlla titoli azionari ogni 5 minuti e invia
un alert su Telegram quando una checklist di segnali (prezzo, volume,
medie mobili, notizie) supera una soglia configurabile.

⚠️ **Disclaimer**: questo è uno strumento informativo basato su regole
semplici. Non è consulenza finanziaria, non prevede il futuro e non
garantisce nulla. Le decisioni di investimento restano sempre tue.

---

## 1. Crea il bot Telegram

1. Su Telegram, cerca **@BotFather** e avvia una chat.
2. Invia `/newbot` e segui le istruzioni (nome e username del bot).
3. BotFather ti darà un **token** tipo `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Salvalo.
4. Cerca il tuo nuovo bot su Telegram e invia un qualsiasi messaggio (es. "ciao") per attivare la chat.
5. Recupera il tuo **chat_id** aprendo nel browser (sostituendo il token):
   ```
   https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates
   ```
   Nel JSON di risposta cerca `"chat":{"id": ...}` — quel numero è il tuo chat_id.

## 2. Crea il repository su GitHub

1. Crea un nuovo repository (può essere privato) su GitHub.
2. Carica tutti i file di questo progetto (`main.py`, `config.yaml`,
   `requirements.txt`, cartella `.github/workflows/`).

## 3. Configura i "Secrets" del repository

Su GitHub: **Settings → Secrets and variables → Actions → New repository secret**

Aggiungi due secret:
- `TELEGRAM_BOT_TOKEN` → il token ottenuto da BotFather
- `TELEGRAM_CHAT_ID` → il chat_id ottenuto al punto 1.5

## 4. Personalizza `config.yaml`

- Modifica la lista `tickers` con i titoli fissi che vuoi seguire sempre
  (usa il formato Yahoo Finance, es. `AAPL`, `MSFT`, `NVDA`).
- Regola le soglie in `thresholds` secondo la tua sensibilità (soglie più
  basse = più alert, anche meno significativi).
- Personalizza `news_keywords` con termini rilevanti per i tuoi settori.

### Scouting automatico (sezione `scouting`)

Oltre alla watchlist fissa, il bot fa "scouting" sui titoli **S&P 500**
(mercato USA): ad ogni ciclo scarica in blocco i dati giornalieri di tutto
l'indice, individua i titoli con variazione % o volume più anomali del
momento (i "top mover"), e applica loro la stessa checklist dettagliata.
Ricevi così alert sia sui tuoi titoli fissi (📌 Watchlist) sia su nuove
opportunità trovate automaticamente (🔍 Scouting).

Parametri configurabili:
- `top_n`: quanti top mover analizzare in dettaglio ad ogni ciclo (default 10)
- `min_abs_change_pct`: variazione % giornaliera minima per essere "interessante"
- `min_volume_ratio`: volume minimo (rispetto alla media) per essere "interessante"

**Nota su affidabilità e tempi**: la lista dei componenti S&P 500 viene
scaricata da Wikipedia ad ogni esecuzione; se il sito non è raggiungibile
il bot usa automaticamente una lista di riserva statica (meno aggiornata
ma funzionante). Lo scan dell'intero indice richiede una chiamata "bulk"
a Yahoo Finance che può richiedere qualche decina di secondi.

## 5. Attiva il workflow

1. Vai nella tab **Actions** del repository.
2. Se richiesto, abilita i workflow.
3. Il workflow "Monitoraggio Azioni" partirà automaticamente ogni 5 minuti.
   Puoi anche avviarlo manualmente da Actions → Monitoraggio Azioni → "Run workflow".

## Note importanti

- **Frequenza reale**: GitHub Actions esegue i cron "best effort": nei
  momenti di alto carico può ritardare di qualche minuto rispetto ai 5 previsti.
- **Repository inattivi**: GitHub disabilita automaticamente i workflow
  schedulati dopo 60 giorni senza attività sul repository. Basta un
  commit o un avvio manuale per riattivarli.
- **Dati**: i prezzi arrivano da Yahoo Finance tramite la libreria
  `yfinance` (gratuita, con qualche minuto di ritardo tipico per i dati
  intraday, non tick-by-tick).
- **Le news** vengono lette da feed RSS pubblici: puoi aggiungerne altri
  modificando `rss_feeds` in `config.yaml`.
- **Costi**: GitHub Actions è gratuito fino a un certo numero di minuti/mese
  per repository pubblici e privati (piano free), sufficiente per questo uso.

## Come leggere gli alert

Ogni messaggio Telegram mostra:
- Il ticker e il prezzo attuale
- Il punteggio totale della checklist
- L'elenco dei motivi che hanno fatto scattare l'alert
- Eventuali notizie correlate trovate

Il punteggio è la somma di 4 possibili fattori (prezzo a 5 minuti, prezzo
giornaliero, volume anomalo, incrocio medie mobili) + 1 fattore news.
Modifica `alert_score_threshold` in `config.yaml` per decidere quanto
essere selettivo.
