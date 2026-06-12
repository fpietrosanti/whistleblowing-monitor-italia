# Whistleblowing Monitor Italia

## Documento di Progetto

**Versione:** 1.0
**Data:** 12 Giugno 2026
**Autore:** Fabio Chiusano — infosecurity.ch
**Stato:** Bozza per revisione

---

# PARTE I — Obiettivi, Metodologia e Risultati Attesi

## 1. Contesto

Il D.Lgs. 24/2023 (recepimento della Direttiva UE 2019/1937) impone a tutte le pubbliche
amministrazioni italiane di istituire canali interni di segnalazione per la tutela delle
persone che segnalano violazioni del diritto dell'Unione e delle disposizioni normative
nazionali (c.d. whistleblowing). L'ANAC è l'autorità designata per la gestione delle
segnalazioni esterne e per la vigilanza sull'adozione dei canali interni.

Nonostante l'obbligo normativo, non esiste ad oggi un monitoraggio sistematico e
pubblico dell'effettiva disponibilità e accessibilità dei canali digitali di segnalazione
presso le pubbliche amministrazioni italiane.

## 2. Obiettivi del Progetto

### 2.1 Obiettivo Generale

Realizzare un sistema di monitoraggio automatizzato, periodico e pubblicamente
accessibile che censisca lo stato di attuazione dei canali digitali di whistleblowing
presso tutte le pubbliche amministrazioni italiane censite nell'Indice delle Pubbliche
Amministrazioni (IndicePA).

### 2.2 Obiettivi Specifici

1. **Censimento universale**: Analizzare i siti web di tutte le PA presenti in IndicePA
   (~23.000 enti) per identificare la presenza di sezioni dedicate al whistleblowing
   e alla segnalazione di illeciti.

2. **Valutazione dell'accessibilità dei canali digitali**: Per ogni PA in cui viene
   identificato un canale digitale di segnalazione, determinare:
   - Se il canale è raggiungibile via internet pubblica
   - Se è accessibile senza autenticazione o identificazione come utente/dipendente interno
   - Se supporta la segnalazione in forma anonima
   - Se richiede autenticazione/identificazione forte (SPID, CIE, credenziali interne)

3. **Identificazione del software di segnalazione**: Per ogni canale digitale accessibile,
   identificare il software o la piattaforma utilizzata (es. GlobaLeaks, Legality
   Whistleblowing, WhistleblowerSoftware.com, soluzioni custom, ecc.).

4. **Mappatura dei contatti RPCT**: Identificare se sul sito della PA è pubblicato il
   contatto (email e/o telefono) del Responsabile della Prevenzione della Corruzione
   e della Trasparenza (RPCT).

5. **Mappatura dei canali di segnalazione non digitali**: Identificare se è disponibile
   un indirizzo email o un numero di telefono come canale alternativo di segnalazione.

6. **Valutazione della policy di whistleblowing**: Verificare se la PA pubblica una
   policy/procedura di whistleblowing accessibile, e archiviarne il documento PDF
   ove disponibile.

7. **Monitoraggio longitudinale**: Eseguire l'intera analisi su base mensile,
   registrando e rendendo visibili le variazioni nel tempo.

8. **Pubblicazione Open Data**: Rendere tutti i dati raccolti disponibili in formato
   aperto (CSV, Excel, JSON) per il riuso da parte di ricercatori, giornalisti,
   organizzazioni della società civile e istituzioni.

## 3. Metodologia

### 3.1 Fonte dati primaria

L'elenco delle pubbliche amministrazioni è ottenuto dall'**Indice delle Pubbliche
Amministrazioni (IndicePA)**, gestito da AgID, acceduto tramite la sua API CKAN JSON.
Per ogni ente vengono acquisiti: codice amministrazione, denominazione, sito web
istituzionale, categoria, regione, provincia.

### 3.2 Processo di analisi (per ciascuna PA)

Il processo di analisi per ciascun ente segue quattro fasi sequenziali:

**Fase 1 — Discovery**
Accesso al sito istituzionale della PA e ricerca automatizzata di pagine relative
al whistleblowing mediante:
- Navigazione dei menu principali e della sezione "Amministrazione Trasparente"
- Ricerca di keyword specifiche: "whistleblowing", "segnalazione illeciti",
  "anticorruzione", "RPCT", "segnalazioni", "tutela del segnalante"
- Analisi dei link nella sezione "Amministrazione Trasparente > Altri contenuti >
  Prevenzione della Corruzione"

**Fase 2 — Probing del canale digitale**
Per ogni canale digitale individuato:
- Verifica della raggiungibilità HTTP/HTTPS
- Verifica dell'accessibilità senza credenziali
- Rilevamento di richieste di autenticazione forte (SPID, CIE, login interno)
- Verifica del supporto alla segnalazione anonima (analisi del form/interfaccia)

**Fase 3 — Fingerprinting del software**
Identificazione del software di whistleblowing tramite:
- Analisi dei meta tag HTML, header HTTP, favicon
- Pattern matching su path noti (es. `/whistleblower/`, `/#/`)
- Riconoscimento di firme specifiche nel DOM (classi CSS, titoli, footer)

**Fase 4 — Raccolta informazioni complementari**
- Ricerca dei contatti RPCT (email, telefono) nella sezione "Amministrazione Trasparente"
- Ricerca di canali di segnalazione alternativi (email, telefono, posta ordinaria)
- Download e archiviazione della policy di whistleblowing in formato PDF
- Registrazione della visibilità/accessibilità della policy

### 3.3 Frequenza

L'analisi completa viene eseguita con cadenza **mensile**. Ad ogni esecuzione vengono
calcolate le differenze rispetto alla scansione precedente.

### 3.4 Parallelismo e impatto

Per limitare il carico sui siti delle PA, l'analisi procede con un massimo di
**5 enti in parallelo**, con tempi di attesa adeguati tra le richieste.

### 3.5 Gestione siti complessi

Alcuni siti web delle PA potrebbero utilizzare tecnologie di rendering lato client
(SPA, JavaScript-heavy) che impediscono l'analisi tramite semplice parsing HTML.
In questi casi, il sistema è predisposto per utilizzare un browser headless completo
(Chromium via Playwright) come fallback automatico.

## 4. Risultati Attesi e Indicatori (KPI)

### 4.1 KPI Primari

| # | Indicatore | Descrizione |
|---|-----------|-------------|
| K1 | **Copertura canale digitale** | % di PA con almeno un canale digitale di whistleblowing attivo e raggiungibile |
| K2 | **Accessibilità pubblica** | % di PA il cui canale digitale è accessibile da internet senza autenticazione interna |
| K3 | **Supporto anonimato** | % di PA il cui canale digitale consente la segnalazione anonima |
| K4 | **Autenticazione forte** | % di PA che richiedono autenticazione forte (SPID/CIE) per la segnalazione |
| K5 | **Software utilizzato** | Distribuzione dei software di whistleblowing per numero di PA |
| K6 | **Contatto RPCT pubblicato** | % di PA che pubblicano email e/o telefono del RPCT |
| K7 | **Canale email/telefono** | % di PA che offrono email o telefono come canale di segnalazione |
| K8 | **Policy visibile** | % di PA che pubblicano una policy/procedura di whistleblowing accessibile |
| K9 | **Policy PDF archiviata** | Numero di documenti PDF di policy archiviati |

### 4.2 KPI di Trend (mensili)

| # | Indicatore | Descrizione |
|---|-----------|-------------|
| T1 | **Nuove attivazioni** | PA che hanno attivato un canale digitale rispetto al mese precedente |
| T2 | **Disattivazioni** | PA che hanno rimosso o reso irraggiungibile il canale |
| T3 | **Cambi software** | PA che hanno cambiato software di segnalazione |
| T4 | **Cambi policy** | PA che hanno aggiornato/rimosso la policy |

### 4.3 Breakdown dimensionali

Tutti i KPI sono disponibili con breakdown per:
- **Regione** (20 regioni italiane)
- **Categoria PA** (Comuni, Province, Regioni, Università, ASL, ecc.)
- **Fascia dimensionale** (ove disponibile)

## 5. Output e Deliverable

### 5.1 Dashboard Web Pubblica

Un'interfaccia web accessibile pubblicamente senza autenticazione che presenta:
- Vista aggregata con i KPI principali
- Ricerca per singola PA (denominazione, codice, regione)
- Dettaglio per singola PA con storico scansioni
- Visualizzazioni geografiche e categoriali
- Trend temporali

### 5.2 Open Data

Export automatici aggiornati ad ogni scansione:
- **CSV**: tabella piatta con tutti i campi, pronta per analisi in spreadsheet
- **Excel (.xlsx)**: versione formattata con più fogli (dati, KPI, diff mensile)
- **JSON**: formato strutturato per integrazione programmatica

Tutti i file sono scaricabili liberamente dalla dashboard.

### 5.3 Archivio Policy

Raccolta di tutti i documenti PDF di policy di whistleblowing scaricati,
organizzati per PA e data di raccolta. Disponibili per download dalla dashboard.

## 6. Limiti e Disclaimer

- L'analisi è condotta esclusivamente su informazioni pubblicamente accessibili
  sui siti web istituzionali delle PA.
- L'identificazione del software è basata su tecniche di fingerprinting e potrebbe
  non essere accurata al 100% in caso di personalizzazioni significative.
- La valutazione del supporto all'anonimato è basata sull'analisi dell'interfaccia
  visibile e non su test funzionali di invio segnalazioni.
- Il sistema non effettua invio di segnalazioni di prova né crea account.
- Il crawling è condotto nel rispetto del `robots.txt` e con rate limiting adeguato.

## 7. Quadro Normativo di Riferimento

- **Direttiva (UE) 2019/1937** — Protezione delle persone che segnalano violazioni
  del diritto dell'Unione
- **D.Lgs. 10 marzo 2023, n. 24** — Recepimento italiano della Direttiva
- **Linee guida ANAC** — Approvate con Delibera n. 311 del 12 luglio 2023
- **D.Lgs. 33/2013** — Obblighi di pubblicità, trasparenza e diffusione di informazioni
  (Amministrazione Trasparente)

---

# PARTE II — Implementazione Tecnica

## 8. Architettura del Sistema

### 8.1 Componenti principali

```
┌─────────────────────────────────────────────────────────┐
│                    WB Monitor Italia                     │
│                                                         │
│  ┌──────────┐  ┌───────────┐  ┌───────┐  ┌──────────┐ │
│  │  Ingest   │→│ Discovery  │→│ Probe  │→│ Reporter  │ │
│  │ (IndicePA)│  │ (Crawler)  │  │       │  │(DB+Export)│ │
│  └──────────┘  └───────────┘  └───────┘  └──────────┘ │
│        │              │            │            │       │
│        └──────────────┴────────────┴────────────┘       │
│                        │                                 │
│                   ┌────┴────┐                            │
│                   │ SQLite  │                            │
│                   │   DB    │                            │
│                   └────┬────┘                            │
│                        │                                 │
│              ┌─────────┴─────────┐                      │
│              │   Web Dashboard   │                       │
│              │  (uvicorn + TLS)  │                       │
│              └───────────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

### 8.2 Stack Tecnologico

| Componente | Tecnologia | Motivazione |
|---|---|---|
| Linguaggio | Python 3.11+ | Ecosistema maturo per scraping, web e data |
| HTTP client | `httpx` (async) | Supporto async nativo, HTTP/2, timeout robusti |
| HTML parsing | `beautifulsoup4` + `lxml` | Parsing robusto di HTML malformato |
| Browser headless | `playwright` (Chromium) | Fallback per siti con rendering JS |
| Database | SQLite 3 | Zero-config, portabile, adeguato al volume dati |
| Web framework | `FastAPI` + `Jinja2` | Async, veloce, template HTML server-side |
| Web server | `uvicorn` con TLS nativo | No reverse proxy, certificati Let's Encrypt diretti |
| TLS/ACME | `certbot` (standalone) o libreria ACME Python | Rinnovo automatico certificati |
| Export dati | `pandas` + `openpyxl` | Generazione CSV, XLSX, JSON |
| Scheduling | `cron` (sistema) | Affidabile, standard, già presente |
| PDF download | `httpx` | Download diretto dei PDF delle policy |

### 8.3 Webserver e TLS

Il webserver è `uvicorn` in esecuzione diretta sulla porta 443 con supporto TLS nativo.
I certificati Let's Encrypt vengono ottenuti e rinnovati automaticamente tramite
`certbot` in modalità standalone (challenge HTTP-01 sulla porta 80). Un cron job
gestisce il rinnovo periodico e il reload del certificato.

Non viene utilizzato alcun reverse proxy (nginx, caddy, ecc.). Il processo Python
ascolta direttamente sulle porte 80 (redirect a HTTPS) e 443 (applicazione).

### 8.4 Gestione Siti Complessi (Piano B)

Lo scraper opera in due modalità:

1. **Modalità leggera (default)**: `httpx` + `beautifulsoup4` — veloce, basso consumo
   risorse, adeguato per la maggior parte dei siti.

2. **Modalità browser completa (fallback)**: `playwright` con Chromium headless —
   attivata automaticamente quando:
   - La pagina restituisce un body vuoto o minimale (< 1KB di testo)
   - Viene rilevato un framework SPA (React, Angular, Vue) senza SSR
   - Il response contiene `noscript` con contenuti indicativi di rendering JS-only
   - La modalità leggera fallisce dopo 2 tentativi

   Il sistema registra nel DB quale modalità è stata utilizzata per ogni PA, per
   ottimizzare le scansioni successive.

## 9. Schema Database

### 9.1 Tabelle principali

```sql
-- Registro scansioni
CREATE TABLE scan_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    total_pa        INTEGER,
    scanned_pa      INTEGER,
    errors          INTEGER,
    status          TEXT DEFAULT 'running'  -- running, completed, failed
);

-- Anagrafica PA (da IndicePA)
CREATE TABLE pa (
    cod_amm         TEXT PRIMARY KEY,
    denominazione   TEXT NOT NULL,
    sito_web        TEXT,
    categoria       TEXT,
    regione         TEXT,
    provincia       TEXT,
    comune          TEXT,
    tipologia       TEXT,
    indirizzo       TEXT,
    cap             TEXT,
    cf              TEXT,             -- codice fiscale
    updated_at      TIMESTAMP
);

-- Risultato scansione per PA
CREATE TABLE pa_scan (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id             INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm                 TEXT NOT NULL REFERENCES pa(cod_amm),
    scanned_at              TIMESTAMP NOT NULL,

    -- Stato sito web
    site_reachable          BOOLEAN,
    site_http_status        INTEGER,
    site_error              TEXT,
    render_mode             TEXT,       -- 'light' o 'browser'

    -- Discovery whistleblowing
    wb_section_found        BOOLEAN,
    wb_section_url          TEXT,
    wb_page_html            TEXT,       -- snapshot HTML della pagina WB

    -- Canale digitale
    wb_digital_channel      BOOLEAN,    -- canale digitale presente
    wb_channel_url          TEXT,       -- URL di accesso al canale
    wb_channel_reachable    BOOLEAN,    -- raggiungibile da internet
    wb_channel_type         TEXT,       -- 'platform', 'form', 'email_only'

    -- Accessibilità e anonimato
    wb_requires_auth        BOOLEAN,    -- richiede login per accedere
    wb_auth_type            TEXT,       -- 'none', 'spid', 'cie', 'internal', 'other'
    wb_anonymous_allowed    BOOLEAN,    -- segnalazione anonima possibile
    wb_strong_auth_required BOOLEAN,    -- SPID/CIE/identificazione forte

    -- Software
    wb_software             TEXT,       -- nome software identificato
    wb_software_version     TEXT,       -- versione se rilevabile
    wb_software_confidence  REAL,       -- 0.0-1.0, confidenza fingerprinting

    -- Contatti RPCT
    rpct_email              TEXT,
    rpct_phone              TEXT,
    rpct_name               TEXT,

    -- Canali segnalazione alternativi
    wb_email                TEXT,       -- email per segnalazione
    wb_phone                TEXT,       -- telefono per segnalazione

    -- Policy
    wb_policy_visible       BOOLEAN,    -- policy visibile sul sito
    wb_policy_url           TEXT,       -- URL della policy
    wb_policy_pdf_path      TEXT,       -- path locale del PDF archiviato
    wb_policy_pdf_hash      TEXT,       -- SHA256 del PDF per rilevare cambiamenti

    -- Metadati
    scan_duration_s         REAL,
    notes                   TEXT
);

-- Diff tra scansioni consecutive
CREATE TABLE pa_scan_diff (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL REFERENCES scan_run(id),
    prev_scan_run_id INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm         TEXT NOT NULL REFERENCES pa(cod_amm),
    field_name      TEXT NOT NULL,      -- nome del campo cambiato
    old_value       TEXT,
    new_value       TEXT,
    detected_at     TIMESTAMP NOT NULL
);

-- Log errori per tracciabilità
CREATE TABLE scan_error_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm         TEXT,
    phase           TEXT,               -- 'discovery', 'probe', 'fingerprint', 'policy'
    error_type      TEXT,
    error_message   TEXT,
    url             TEXT,
    occurred_at     TIMESTAMP NOT NULL
);

-- Indici
CREATE INDEX idx_pa_scan_run ON pa_scan(scan_run_id);
CREATE INDEX idx_pa_scan_cod ON pa_scan(cod_amm);
CREATE INDEX idx_pa_scan_diff_run ON pa_scan_diff(scan_run_id);
CREATE INDEX idx_pa_scan_diff_cod ON pa_scan_diff(cod_amm);
```

## 10. Struttura del Progetto

```
whistleblowing-monitor-italia/
├── src/
│   ├── __init__.py
│   ├── config.py               # Configurazione (paths, DB, parallelismo)
│   ├── ingest.py               # Download e parsing IndicePA
│   ├── discovery.py            # Crawling siti PA per trovare sezione WB
│   ├── probe.py                # Verifica accessibilità e anonimato
│   ├── fingerprint.py          # Identificazione software WB
│   ├── policy.py               # Download e archiviazione policy PDF
│   ├── rpct.py                 # Estrazione contatti RPCT
│   ├── scanner.py              # Orchestratore pipeline completa
│   ├── differ.py               # Calcolo diff tra scansioni
│   ├── exporter.py             # Export CSV, XLSX, JSON
│   ├── browser.py              # Fallback Playwright per siti complessi
│   ├── db.py                   # Gestione database SQLite
│   └── web/
│       ├── app.py              # FastAPI application
│       ├── routes.py           # Endpoint API e pagine
│       └── templates/
│           ├── base.html
│           ├── index.html      # Dashboard KPI
│           ├── search.html     # Ricerca PA
│           ├── detail.html     # Dettaglio singola PA
│           ├── opendata.html   # Pagina download open data
│           └── trend.html      # Trend temporali
├── data/
│   ├── db/                     # Database SQLite
│   ├── exports/                # File CSV, XLSX, JSON generati
│   └── policies/               # PDF archiviati (organizzati per cod_amm/)
├── tests/
│   ├── test_ingest.py
│   ├── test_discovery.py
│   ├── test_probe.py
│   ├── test_fingerprint.py
│   └── test_exporter.py
├── scripts/
│   ├── setup_server.sh         # Setup utente e dipendenze sul server
│   ├── run_scan.sh             # Lancio scansione (invocato da cron)
│   └── renew_cert.sh           # Rinnovo certificato Let's Encrypt
├── requirements.txt
├── pyproject.toml
├── README.md
├── LICENSE
└── PROJECT_DOCUMENT.md         # Questo documento
```

## 11. Fingerprinting Software Whistleblowing

### 11.1 Software noti e pattern di riconoscimento

| Software | Pattern identificativi |
|---|---|
| **GlobaLeaks** | Path `/#/`, title contiene "GlobaLeaks" o "Globaleaks", meta generator, classe CSS `ng-app`, API endpoint `/api/public` |
| **Legality Whistleblowing** | Dominio `*.legality.it` o `*.legalitywhistleblowing.it`, title/footer "Legality" |
| **WhistleblowerSoftware.com** | Dominio `*.whistleblowersoftware.com`, iframe specifici |
| **Segnalazioni.net** | Dominio `*.segnalazioni.net`, title specifico |
| **ISWEB** | Pattern ISWEB nel footer/meta |
| **Comunica WB** | Pattern specifici ComunicaWB |
| **Custom/Interno** | Form HTML nativi senza fingerprint di prodotti noti |

La lista verrà aggiornata e arricchita durante lo sviluppo e le prime scansioni.

## 12. Export Open Data

Ad ogni scansione completata, vengono generati automaticamente:

### 12.1 CSV (`pa_whistleblowing_YYYY-MM.csv`)
Tabella piatta con una riga per PA, tutti i campi principali. Encoding UTF-8 con BOM
per compatibilità Excel.

### 12.2 Excel (`pa_whistleblowing_YYYY-MM.xlsx`)
Workbook con fogli:
- **Dati**: tabella completa
- **KPI**: riepilogo indicatori
- **Per Regione**: breakdown regionale
- **Per Software**: distribuzione software
- **Diff**: cambiamenti rispetto al mese precedente

### 12.3 JSON (`pa_whistleblowing_YYYY-MM.json`)
Struttura:
```json
{
  "metadata": {
    "scan_date": "2026-06-15",
    "total_pa": 23000,
    "scanned_pa": 22500,
    "version": "1.0"
  },
  "kpi": { ... },
  "data": [ ... ]
}
```

## 13. Infrastruttura di Deployment

### 13.1 Server
- **Host**: `51.158.36.151` (stesso server del selective-copy-trader)
- **Utente dedicato**: `wbmonitor` (home: `/home/wbmonitor`)
- **Nessuna condivisione** di processi o dati con altri servizi sullo stesso server

### 13.2 Porte
- **80**: Redirect HTTP → HTTPS (gestito dall'applicazione Python)
- **443**: Dashboard web con TLS (certificato Let's Encrypt)

### 13.3 Dominio
- **Dominio**: `test.infosecurity.ch`
- **Repository**: `https://github.com/fpietrosanti/whistleblowing-monitor-italia`

### 13.4 Cron Schedule
```cron
# Scansione mensile — primo giorno del mese alle 02:00
0 2 1 * * /home/wbmonitor/whistleblowing-monitor-italia/scripts/run_scan.sh

# Rinnovo certificato — ogni 60 giorni
0 3 1 */2 * /home/wbmonitor/whistleblowing-monitor-italia/scripts/renew_cert.sh
```

### 13.5 Requisiti server
- Python 3.11+
- ~2 GB disco per DB + policy PDF + export (stima iniziale, crescita ~500MB/anno)
- Chromium headless (installato via Playwright)
- Porta 80 e 443 aperte

## 14. Sicurezza e Limiti Etici

- Il sistema effettua **solo lettura** dei siti web pubblici — nessuna modifica,
  nessun invio di dati, nessuna creazione di account.
- Il crawling rispetta il `robots.txt` di ciascun sito.
- Il rate limiting (max 5 PA in parallelo, delay tra richieste) previene il
  sovraccarico dei server delle PA.
- Lo User-Agent si identifica chiaramente come bot di monitoraggio.
- Nessun dato personale viene raccolto oltre ai contatti RPCT pubblicati sul sito
  della PA (dati già pubblici per obbligo di legge).

## 15. Evoluzioni Future (fuori scope Fase 1)

- Analisi contenutistica delle policy di whistleblowing (completezza, conformità
  alle linee guida ANAC)
- Analisi di accessibilità WCAG dei canali digitali
- Verifica della conformità alle Linee Guida AgID
- API pubblica REST per interrogazione programmatica dei dati
- Notifiche automatiche di cambiamenti significativi
- Integrazione con dati ANAC sulle segnalazioni ricevute

---

# PARTE III — Fase 2: Società Quotate sulla Borsa Italiana

## 16. Contesto Normativo Settore Privato

### 16.1 Quadro legislativo applicabile

Il D.Lgs. 24/2023 estende gli obblighi di whistleblowing al settore privato con
un regime differenziato. Le società quotate sulla Borsa Italiana sono soggette a
un intreccio di obblighi derivanti da più fonti normative:

**a) D.Lgs. 24/2023 — Obbligo di canale interno di segnalazione**

Il decreto si applica ai soggetti del settore privato che:
- Hanno impiegato nell'ultimo anno una media di almeno **50 lavoratori subordinati**
  (a tempo indeterminato o determinato); oppure
- Hanno adottato un **Modello di Organizzazione e Gestione ai sensi del D.Lgs.
  231/2001**, indipendentemente dal numero di dipendenti; oppure
- Operano in **settori sensibili** (servizi finanziari, prevenzione del riciclaggio,
  sicurezza dei trasporti, tutela dell'ambiente), indipendentemente dalle dimensioni.

Tutte le società quotate su Euronext Milan rientrano in almeno una (e tipicamente
in tutte) di queste categorie.

**b) D.Lgs. 231/2001 — Responsabilità amministrativa degli enti**

Le società quotate adottano quasi universalmente un Modello di Organizzazione,
Gestione e Controllo (MOG) ai sensi del D.Lgs. 231/2001, che prevede:
- Un **Organismo di Vigilanza (OdV)** indipendente
- Flussi informativi verso l'OdV, incluse le segnalazioni di illeciti
- L'integrazione del canale whistleblowing nel MOG

Il D.Lgs. 24/2023 ha modificato l'art. 6 del D.Lgs. 231/2001, rendendo il canale
di segnalazione interna parte integrante del Modello 231.

**c) Codice di Corporate Governance (ex Codice di Autodisciplina)**

Le società quotate su Euronext Milan aderiscono (su base comply-or-explain) al
Codice di Corporate Governance promosso dal Comitato per la Corporate Governance,
che prevede raccomandazioni su:
- Sistemi di controllo interno e gestione dei rischi
- Ruolo del Comitato Controllo e Rischi
- Flussi informativi sulla compliance

**d) TUF — Testo Unico della Finanza (D.Lgs. 58/1998)**

Le società quotate sono soggette alla vigilanza CONSOB e agli obblighi di
trasparenza e informativa continua previsti dal TUF, inclusa la pubblicazione
della **Relazione sul governo societario e gli assetti proprietari** (art. 123-bis),
che deve contenere informazioni sui sistemi di controllo interno.

### 16.2 Differenze chiave rispetto alle Pubbliche Amministrazioni

| Aspetto | Pubbliche Amministrazioni | Società Quotate |
|---|---|---|
| **Fonte dati anagrafica** | IndicePA (CKAN API) | CONSOB (registro quotate) + Borsa Italiana |
| **Autorità di vigilanza** | ANAC | CONSOB + ANAC (per il canale WB) |
| **Figura di riferimento** | RPCT | OdV (Organismo di Vigilanza) |
| **Obbligo normativo canale** | D.Lgs. 24/2023 | D.Lgs. 24/2023 + D.Lgs. 231/2001 |
| **Sezione web di riferimento** | "Amministrazione Trasparente" | "Governance" / "Investor Relations" / "Compliance" / "Etica" |
| **Pubblicazione obbligatoria** | Obbligo di trasparenza (D.Lgs. 33/2013) | Relazione governo societario (art. 123-bis TUF) |
| **Modello organizzativo** | Piano Triennale Prevenzione Corruzione | MOG 231 |
| **Segnalazione anonima** | Facoltativa ma raccomandata ANAC | Facoltativa, ma molte società la prevedono |

### 16.3 Soggetti legittimati alla segnalazione (settore privato)

Nel settore privato, i soggetti tutelati dal D.Lgs. 24/2023 includono:
- Lavoratori subordinati
- Lavoratori autonomi e collaboratori con rapporto di lavoro con l'ente
- Liberi professionisti e consulenti
- Volontari e tirocinanti (anche non retribuiti)
- Azionisti e persone con funzioni di amministrazione, direzione, controllo,
  vigilanza o rappresentanza
- Candidati, ex dipendenti e persone in fase di selezione

Questo perimetro è più ampio rispetto al settore pubblico e implica che il canale
di segnalazione debba essere accessibile a una platea variegata di soggetti, non
solo ai dipendenti interni.

## 17. Obiettivi Fase 2

### 17.1 Obiettivo Generale

Estendere il monitoraggio alle **società quotate sui mercati gestiti da Borsa
Italiana** (Euronext Milan, Euronext STAR Milan, Euronext Growth Milan), valutando
la conformità dei loro canali di whistleblowing ai requisiti del D.Lgs. 24/2023
e del D.Lgs. 231/2001.

### 17.2 Obiettivi Specifici

1. **Censimento società quotate**: Acquisire l'elenco completo delle società quotate
   dal registro CONSOB (https://www.consob.it/web/area-pubblica/export-quotate) e
   dai listini Borsa Italiana.

2. **Analisi siti web corporate**: Per ogni società quotata, analizzare il sito web
   istituzionale per identificare:
   - Sezione dedicata al whistleblowing / segnalazione illeciti
   - Informazioni sul Modello 231 e sull'Organismo di Vigilanza
   - Policy di whistleblowing / procedura per la gestione delle segnalazioni
   - Codice Etico (che tipicamente contiene riferimenti al canale WB)

3. **Valutazione del canale digitale**: Con gli stessi criteri della Fase 1:
   - Raggiungibilità da internet
   - Accessibilità senza credenziali interne
   - Supporto segnalazione anonima
   - Richiesta di autenticazione/identificazione forte
   - Software utilizzato

4. **Mappatura contatti OdV**: Identificare se la società pubblica i contatti
   (email, PEC, indirizzo) dell'Organismo di Vigilanza sul proprio sito web.

5. **Canali di segnalazione alternativi**: Rilevare se esistono canali email,
   telefono, posta ordinaria per le segnalazioni.

6. **Archiviazione documentale**: Download e archiviazione di:
   - Policy / procedura di whistleblowing (PDF)
   - Codice Etico (PDF)
   - Estratto Modello 231 relativo alle segnalazioni (se pubblicato)

### 17.3 KPI specifici Fase 2

| # | Indicatore | Descrizione |
|---|-----------|-------------|
| Q1 | **Copertura canale digitale** | % di società quotate con canale digitale WB attivo |
| Q2 | **Accessibilità pubblica** | % con canale accessibile senza credenziali interne |
| Q3 | **Supporto anonimato** | % con possibilità di segnalazione anonima |
| Q4 | **Autenticazione forte** | % che richiedono identificazione forte |
| Q5 | **Software utilizzato** | Distribuzione per software |
| Q6 | **Contatti OdV pubblicati** | % che pubblicano contatti dell'OdV |
| Q7 | **Policy WB pubblicata** | % che pubblicano la procedura WB |
| Q8 | **Codice Etico pubblicato** | % che pubblicano il Codice Etico |
| Q9 | **MOG 231 pubblicato** | % che pubblicano (estratto del) Modello 231 |

Breakdown per:
- **Mercato**: Euronext Milan / STAR Milan / Growth Milan
- **Settore ATECO / ICB** (Industry Classification Benchmark)
- **Capitalizzazione**: large cap, mid cap, small cap
- **Indice di appartenenza**: FTSE MIB, FTSE Italia Mid Cap, ecc.

## 18. Fonte Dati — Registro Società Quotate

### 18.1 CONSOB — Export Quotate

La fonte primaria è il registro CONSOB delle società quotate, disponibile per
download all'indirizzo:
- https://www.consob.it/web/area-pubblica/export-quotate

Il registro contiene: denominazione, codice fiscale, sede legale, mercato di
quotazione, settore, e altre informazioni anagrafiche.

### 18.2 Borsa Italiana — Listini

Dati complementari sui listini e sulla classificazione delle società:
- Euronext Milan: https://www.borsaitaliana.it/borsa/azioni/all-share/lista.html
- Euronext STAR Milan: https://www.borsaitaliana.it/borsa/azioni/euronext-star-milan/lista.html
- Euronext Growth Milan: https://www.borsaitaliana.it/borsa/azioni/euronext-growth-milan/lista.html

### 18.3 Integrazione dati

I dati CONSOB e Borsa Italiana vengono integrati per ottenere un record completo
per ogni società: denominazione, sito web, mercato, settore, capitalizzazione,
indici di appartenenza.

## 19. Implementazione Tecnica Fase 2

### 19.1 Estensioni al database

```sql
-- Anagrafica società quotate
CREATE TABLE listed_company (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name        TEXT NOT NULL,
    ticker              TEXT,
    isin                TEXT,
    codice_fiscale      TEXT,
    sede_legale         TEXT,
    sito_web            TEXT,
    market              TEXT,       -- 'euronext_milan', 'star_milan', 'growth_milan'
    sector              TEXT,       -- settore ICB o ATECO
    market_cap_class    TEXT,       -- 'large', 'mid', 'small', 'micro'
    ftse_index          TEXT,       -- 'ftse_mib', 'mid_cap', 'small_cap', 'growth'
    consob_id           TEXT,       -- identificativo CONSOB
    updated_at          TIMESTAMP
);

-- Risultato scansione per società quotata
CREATE TABLE company_scan (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id             INTEGER NOT NULL REFERENCES scan_run(id),
    company_id              INTEGER NOT NULL REFERENCES listed_company(id),
    scanned_at              TIMESTAMP NOT NULL,

    -- Stato sito web
    site_reachable          BOOLEAN,
    site_http_status        INTEGER,
    site_error              TEXT,
    render_mode             TEXT,

    -- Discovery whistleblowing
    wb_section_found        BOOLEAN,
    wb_section_url          TEXT,
    wb_page_html            TEXT,

    -- Canale digitale (stessi campi della PA)
    wb_digital_channel      BOOLEAN,
    wb_channel_url          TEXT,
    wb_channel_reachable    BOOLEAN,
    wb_channel_type         TEXT,

    -- Accessibilità e anonimato
    wb_requires_auth        BOOLEAN,
    wb_auth_type            TEXT,
    wb_anonymous_allowed    BOOLEAN,
    wb_strong_auth_required BOOLEAN,

    -- Software
    wb_software             TEXT,
    wb_software_version     TEXT,
    wb_software_confidence  REAL,

    -- Organismo di Vigilanza (equivalente RPCT per privati)
    odv_email               TEXT,
    odv_pec                 TEXT,
    odv_phone               TEXT,
    odv_address             TEXT,

    -- Canali segnalazione alternativi
    wb_email                TEXT,
    wb_phone                TEXT,
    wb_postal_address       TEXT,

    -- Documenti
    wb_policy_visible       BOOLEAN,
    wb_policy_url           TEXT,
    wb_policy_pdf_path      TEXT,
    wb_policy_pdf_hash      TEXT,
    codice_etico_url        TEXT,
    codice_etico_pdf_path   TEXT,
    codice_etico_pdf_hash   TEXT,
    mog231_url              TEXT,
    mog231_pdf_path         TEXT,
    mog231_pdf_hash         TEXT,

    -- Metadati
    scan_duration_s         REAL,
    notes                   TEXT
);

-- Diff per società quotate
CREATE TABLE company_scan_diff (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id         INTEGER NOT NULL REFERENCES scan_run(id),
    prev_scan_run_id    INTEGER NOT NULL REFERENCES scan_run(id),
    company_id          INTEGER NOT NULL REFERENCES listed_company(id),
    field_name          TEXT NOT NULL,
    old_value           TEXT,
    new_value           TEXT,
    detected_at         TIMESTAMP NOT NULL
);
```

### 19.2 Adattamenti alla pipeline di Discovery

La discovery per le società quotate richiede keyword e percorsi diversi:

| Contesto | PA (Fase 1) | Società Quotate (Fase 2) |
|---|---|---|
| **Sezioni target** | "Amministrazione Trasparente", "Anticorruzione" | "Governance", "Investor Relations", "Compliance", "Etica e Integrità", "Sostenibilità" |
| **Keyword** | "RPCT", "segnalazione illeciti", "anticorruzione" | "OdV", "Organismo di Vigilanza", "Modello 231", "Codice Etico", "canale segnalazione", "whistleblowing" |
| **Documenti** | Policy WB | Policy WB, Codice Etico, MOG 231 |
| **Contatti** | RPCT email/telefono | OdV email/PEC/telefono/indirizzo |

### 19.3 Export Open Data Fase 2

I dati delle società quotate vengono esportati in file separati:
- `quotate_whistleblowing_YYYY-MM.csv`
- `quotate_whistleblowing_YYYY-MM.xlsx`
- `quotate_whistleblowing_YYYY-MM.json`

Con gli stessi criteri di formato della Fase 1.

### 19.4 Dashboard — Estensioni

La dashboard web viene estesa con:
- Sezione dedicata "Società Quotate" con KPI specifici
- Ricerca per società (denominazione, ticker, ISIN)
- Filtri per mercato, settore, indice
- Confronto PA vs Quotate sugli indicatori comuni
- Download open data separati per PA e quotate

## 20. Pianificazione Fasi

| Fase | Scope | Prerequisito |
|---|---|---|
| **Fase 1** | Tutte le PA da IndicePA (~23.000 enti) | — |
| **Fase 2** | Società quotate su Borsa Italiana (~400 enti) | Fase 1 completata e rilasciata |

La Fase 2 riutilizza l'intera infrastruttura della Fase 1 (scanner, fingerprinting,
dashboard, export) estendendola con i moduli specifici per le società quotate
(ingest CONSOB, discovery corporate, contatti OdV, documenti MOG/Codice Etico).

## 21. Quadro Normativo di Riferimento (integrato Fase 2)

- **Direttiva (UE) 2019/1937** — Protezione delle persone che segnalano violazioni
  del diritto dell'Unione
- **D.Lgs. 10 marzo 2023, n. 24** — Recepimento italiano della Direttiva
  (settore pubblico e privato)
- **Linee guida ANAC** — Delibera n. 311 del 12 luglio 2023
- **D.Lgs. 8 giugno 2001, n. 231** — Responsabilità amministrativa degli enti
  (Modello Organizzativo, OdV)
- **D.Lgs. 24 febbraio 1998, n. 58 (TUF)** — Testo Unico della Finanza
  (obblighi informativi società quotate, art. 123-bis)
- **Codice di Corporate Governance** — Comitato per la Corporate Governance
  (raccomandazioni su controllo interno)
- **D.Lgs. 14 marzo 2013, n. 33** — Obblighi di pubblicità, trasparenza e
  diffusione di informazioni (Amministrazione Trasparente, solo PA)
- **Regolamento CONSOB Emittenti** — Obblighi informativi per le società quotate

---

*Documento predisposto per revisione da parte di esperti in materia di anticorruzione,
trasparenza amministrativa, corporate governance e protezione del whistleblower.*
