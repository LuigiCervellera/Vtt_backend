# WizVTT Backend

Il backend per **WizVTT** (Virtual Tabletop), un'applicazione per giochi di ruolo che offre mappe in tempo reale, token, gestione personaggi e chat, sviluppato in Python utilizzando **Quart** (framework asincrono compatibile con Flask).

## Stack Tecnologico

- **Framework Web**: [Quart](https://pgjones.gitlab.io/quart/)
- **Database ORM**: [Tortoise ORM](https://tortoise.github.io/) con driver asincrono `asyncpg` per **PostgreSQL**
- **Real-time / WebSocket**: Gestiti nativamente da Quart
- **Gestione Stato in tempo reale**: In memoria via WebSocket (con dizionari Python standard)
- **Gestione Auth**: JWT (JSON Web Tokens)

## Prerequisiti

- Python 3.10 o superiore
- **PostgreSQL** 15+ (in locale o tramite Docker)

## Installazione

1. Clona il repository o naviga nella cartella del progetto:
   ```bash
   cd Vtt_backend
   ```

2. Crea e attiva un ambiente virtuale:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Su Windows: venv\Scripts\activate
   ```

3. Installa le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```

## Configurazione

Il progetto utilizza un file `.env` per la gestione dei segreti e delle configurazioni.

1. Copia il file di esempio per creare la tua configurazione:
   ```bash
   cp .env.example .env
   ```

2. Apri il file `.env` appena creato e verifica o modifica le variabili:
   - `ALLOWED_ORIGINS`: URL del frontend (es. `http://localhost:5173`)
   - `JWT_SECRET`: Chiave segreta per cifrare i token JWT (cambiarla in produzione!)
   - `DATABASE_URL`: Stringa di connessione al DB PostgreSQL (es. `postgres://postgres:postgres@localhost:5432/vtt_db`)
   - `BASE_URL`: URL base del backend, utilizzato per costruire i link alle mappe caricate

### Avvio Rapido del Database con Docker (Consigliato per Sviluppo)

Se hai Docker installato sul tuo sistema, puoi avviare un database PostgreSQL locale già configurato eseguendo nella cartella del progetto:

```bash
docker compose up -d
```

Questo avvierà un'istanza PostgreSQL ascoltando sulla porta `5432` con credenziali preconfigurate corrispondenti a quelle presenti nel file `.env.example`.

## Inizializzazione del Database

Prima di avviare il server, è necessario generare lo schema del database (tabelle per Utenti, Campagne, Personaggi, Mappe).
Esegui lo script apposito (che leggerà automaticamente la stringa dal `.env`):

```bash
python generate_db.py
```

## Esecuzione del Server

Avvia l'applicazione in ambiente di sviluppo (il server resterà in ascolto sulla porta `5000`):

```bash
python main.py
```

Il server sarà accessibile su `http://127.0.0.1:5000/`.
È inoltre disponibile la documentazione API (Swagger/OpenAPI) auto-generata.

## Architettura e Moduli

Il backend è modulare e organizzato tramite *Blueprints*:
- `auth.py`: Registrazione, login, logout e gestione dell'account.
- `campaigns.py`: Creazione e gestione delle stanze di gioco, inviti.
- `characters.py`: Creazione schede e personaggi associati alle campagne.
- `maps.py`: Upload (salvataggio locale nella cartella `uploads/`) e gestione delle mappe per il tavolo da gioco.
- `websocket.py`: Gestione delle connessioni in tempo reale (`/ws`), movimento token, messaggi di chat e opzioni della griglia. L'accesso al WS è rigorosamente protetto richiedendo un JWT valido.

## Sicurezza

Tutte le API pubbliche che modificano dati o accedono a risorse utente richiedono un token JWT, verificato tramite il decoratore `@jwt_required`. La connessione WebSocket autentica inizialmente l'utente e lo autorizza in base al suo ruolo (Master o Giocatore) prima di trasmettere le azioni (come muovere token che non gli appartengono).
