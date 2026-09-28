# SupportNova — Fresh installation (without Docker)

This guide installs SupportNova on a clean machine using a locally installed PostgreSQL
server instead of Docker. Every command is listed in order. Windows (PowerShell) is the
primary path; macOS and Linux equivalents are given where they differ.

| Component | Version | Used for |
|---|---|---|
| Python | 3.11 or 3.12 (tested on 3.12.10) | FastAPI backend, both pipelines |
| Node.js | 20.19+ or 22.12+ (tested on 22.22) | React / Vite frontend |
| PostgreSQL | 16 (15+ works) | Application database |
| Git | any recent | Getting the source code |

At the end you will have:

- API on **http://localhost:8000** (Swagger docs at `/docs`)
- Web app on **http://localhost:5173**

---

## 1. Install the prerequisites

### Windows (PowerShell)

Install with `winget` (built into Windows 10/11). Open **PowerShell** and run:

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id PostgreSQL.PostgreSQL.16
winget install -e --id Git.Git
```

The PostgreSQL installer asks for a password for the `postgres` superuser. Remember it;
you need it in step 3. Keep the default port **5432**.

Close and reopen PowerShell so the new programs are on your `PATH`, then check:

```powershell
python --version
node --version
npm --version
git --version
```

`psql` is not added to `PATH` by the installer. Add it for the current session:

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\16\bin"
psql --version
```

> Prefer installers? Download them from python.org (tick **Add python.exe to PATH**),
> nodejs.org (LTS), and enterprisedb.com/downloads/postgres-postgresql-downloads.

### macOS (Homebrew)

```bash
brew install python@3.12 node@22 postgresql@16 git
brew services start postgresql@16
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/opt/node@22/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip postgresql git curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
sudo systemctl enable --now postgresql
```

---

## 2. Get the source code

```powershell
cd D:\
git clone https://github.com/codewithansha/Techwiz7-Generative_AI.git support-nova
cd support-nova
```

(Any folder works; the rest of this guide assumes you are inside the `support-nova` folder.)

---

## 3. Create the database

SupportNova needs a PostgreSQL user and database. The application creates its own tables
and seed data on first start. You only create the empty database.

### Windows

```powershell
psql -U postgres -h localhost -c "CREATE USER supportnova WITH PASSWORD 'supportnova';"
psql -U postgres -h localhost -c "CREATE DATABASE supportnova OWNER supportnova;"
```

Enter the `postgres` password you chose during installation when prompted.

### macOS

```bash
psql postgres -c "CREATE USER supportnova WITH PASSWORD 'supportnova';"
psql postgres -c "CREATE DATABASE supportnova OWNER supportnova;"
```

### Linux

```bash
sudo -u postgres psql -c "CREATE USER supportnova WITH PASSWORD 'supportnova';"
sudo -u postgres psql -c "CREATE DATABASE supportnova OWNER supportnova;"
```

Check that the new user can connect (password `supportnova`):

```powershell
psql -U supportnova -h localhost -d supportnova -c "SELECT version();"
```

> For anything beyond local development, use a strong password here and put the same one
> in `DATABASE_URL` in step 4.

---

## 4. Set up the backend

### 4.1 Create and activate a virtual environment

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell refuses with "running scripts is disabled", you can skip activation and
call the environment's Python directly. Use `.\.venv\Scripts\python.exe` wherever this
guide says `python`, for example `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Your prompt now starts with `(.venv)`.

### 4.2 Install the Python dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4.3 Create the configuration file

Windows:

```powershell
copy .env.example .env
```

macOS / Linux:

```bash
cp .env.example .env
```

Generate a secret key for signing login tokens:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open `.env` in an editor and set at least these values:

```ini
# Paste the generated value
SECRET_KEY=<generated value>

# Matches the user and database created in step 3
DATABASE_URL=postgresql+psycopg://supportnova:supportnova@localhost:5432/supportnova

# Pipeline 1 (GenAI). Fill in at least one key; the others are fallbacks.
GENAI_PROVIDER=openai
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
GROK_API_KEY=

# Administrator created on the very first start. Change the password before any shared use.
BOOTSTRAP_ADMIN_EMAIL=admin@nimbuscarta.example
BOOTSTRAP_ADMIN_PASSWORD=ChangeMeNow!23
```

Notes:

- **Never commit `.env`.** It is already listed in `.gitignore`.
- GenAI keys are optional for starting the app. Without a working key, Pipeline 2 (Python
  validation) still runs and every case is routed to manual review.
- All other settings (thresholds, time budget, upload size) have working defaults. See
  the comments in `.env.example`.

### 4.4 (Optional) Check your GenAI keys

```powershell
python scripts\check_genai_providers.py
```

It lists each configured provider and whether it answered. Keys are never printed.

### 4.5 Start the backend

```powershell
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

On the **first** start the API creates all tables and seeds the NimbusCarta reference
data: departments, categories, the rule matrix, escalation rules, SLAs, policy documents,
prompt templates and demo users. Leave this terminal running.

Check it from a second terminal or your browser:

```powershell
curl.exe http://localhost:8000/health
```

The expected response contains `"status":"ok"` and `"database":"ok"`. Interactive API
docs are at http://localhost:8000/docs.

---

## 5. Set up the frontend

Open a **second** terminal in the project folder:

```powershell
cd frontend
npm ci
copy .env.example .env
npm run dev
```

(macOS/Linux: use `cp .env.example .env`.)

`frontend/.env` holds one setting, `VITE_API_URL=http://localhost:8000`. Change it only
if the API runs somewhere else. In that case, also add the web app's address to
`CORS_ORIGINS` in the backend `.env`.

Open **http://localhost:5173**.

---

## 6. Sign in

The login page has a **Use a demo profile** menu with these seeded accounts:

| Role | Email | Password |
|---|---|---|
| Administrator | admin@nimbuscarta.example | ChangeMeNow!23 (or your `BOOTSTRAP_ADMIN_PASSWORD`) |
| Agent | agent@nimbuscarta.example | AgentPass!23 |
| Reviewer | reviewer@nimbuscarta.example | ReviewPass!23 |
| Manager | manager@nimbuscarta.example | ManagerPass!23 |
| Customer | customer@nimbuscarta.example | CustomerPass!23 |

Change or deactivate these accounts (Settings → Users) before any public deployment.

A quick end-to-end check:

1. **Customer:** New complaint → submit.
2. **Agent:** open it → Assign to me → Analyze complaint.
3. **Reviewer:** Review queue → Approve.
4. **Agent:** set status to Resolved with a note.
5. **Customer:** open the complaint → "Yes, close it".

---

## 7. Running it again later

Two terminals, from the project folder.

Terminal 1 (backend):

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 (frontend):

```powershell
cd frontend
npm run dev
```

On Windows you can also double-click or run the helper scripts, which pick up `.venv`
automatically:

```powershell
scripts\run-api.cmd
scripts\run-web.cmd
```

Make sure PostgreSQL is running first. On Windows it runs as the service
`postgresql-x64-16`: check with `Get-Service postgresql*` and start it with
`Start-Service postgresql-x64-16` (from an administrator PowerShell) if it is stopped.

---

## 8. Running the tests

Unit tests need no database:

```powershell
pytest
```

The API integration tests (roles, the full complaint lifecycle, uploads, configuration,
reports) run against a **separate, disposable** database. The suite drops and recreates
that database's schema, so never point it at your real one. Its name must contain `test`
or `scratch`.

```powershell
psql -U postgres -h localhost -c "CREATE DATABASE supportnova_test OWNER supportnova;"
$env:SUPPORTNOVA_TEST_DATABASE_URL="postgresql+psycopg://supportnova:supportnova@localhost:5432/supportnova_test"
pytest
```

(macOS/Linux: `export SUPPORTNOVA_TEST_DATABASE_URL=postgresql+psycopg://supportnova:supportnova@localhost:5432/supportnova_test`.)

Frontend type-check, lint and production build:

```powershell
cd frontend
npm run build
npm run lint
```

---

## 9. Optional extras

Generate the 500-complaint synthetic dataset in `sample_complaints/nimbuscarta_500.json`:

```powershell
python scripts\generate_complaints.py
```

Build and preview the production frontend:

```powershell
cd frontend
npm run build
npm run preview
```

---

## 10. Starting over with an empty database

This deletes all complaints, uploads and changes. The next API start re-seeds the demo data.

```powershell
psql -U postgres -h localhost -c "DROP DATABASE supportnova;"
psql -U postgres -h localhost -c "CREATE DATABASE supportnova OWNER supportnova;"
Remove-Item -Recurse -Force uploads
```

(macOS/Linux: `rm -rf uploads`.)

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `python` opens the Microsoft Store or "Python was not found" | Python is not on `PATH`. Reinstall with "Add python.exe to PATH", or turn off the `python.exe` App Execution Alias in Windows Settings. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Skip activation and use `.\.venv\Scripts\python.exe` directly (see 4.1). |
| `psql` is not recognized | Add `C:\Program Files\PostgreSQL\16\bin` to `PATH` (step 1). |
| API log shows `connection refused` / health shows `"database":"unavailable"` | PostgreSQL is not running, or `DATABASE_URL` host, port, user or password is wrong. |
| `password authentication failed for user "supportnova"` | The password in `DATABASE_URL` does not match step 3. Reset it with `psql -U postgres -h localhost -c "ALTER USER supportnova WITH PASSWORD 'supportnova';"` |
| `permission denied for schema public` | The database was created without `OWNER supportnova`. Run `psql -U postgres -h localhost -c "ALTER DATABASE supportnova OWNER TO supportnova;"` |
| Web app shows "Cannot reach the SupportNova API" | The backend is not running on port 8000, or `VITE_API_URL` in `frontend/.env` is wrong. Restart `npm run dev` after editing `.env`. |
| Browser console shows a CORS error | Add the web app's exact origin (e.g. `http://localhost:5173`) to `CORS_ORIGINS` in `.env` and restart the API. |
| `Port 5173 is in use` / `address already in use :8000` | Another app uses the port. Stop it, or use `npm run dev -- --port 5180` and `uvicorn ... --port 8001`, and update `VITE_API_URL` and `CORS_ORIGINS` to match. |
| `npm ci` fails with an engine error | Node.js is older than 20.19. Install the current LTS. |
| Analysis says "GenAI failed" and the case goes to manual review | No provider key answered: missing key, no credits, or rate limit. Run `python scripts\check_genai_providers.py`. After fixing a key, go to Settings → Pipelines → **Resume** (or restart the API) to retry providers that were paused. |
| Login works but pages show "Access restricted" | Expected for that role. Each role only sees its own screens. |
