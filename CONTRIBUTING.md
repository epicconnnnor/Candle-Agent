# Contributing

[Home](README.md) · [Technical guide](docs/technical-guide.md)

Use the [installation guide](docs/installation.md) to run the app locally. The additional steps here are for changing the backend or frontend.

## Backend development

Install Python 3.11 or newer. CI currently uses Python 3.12.

From the project folder, create a virtual environment and install development dependencies.

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe .github/scripts/check_utf8.py
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check .
.venv/bin/python .github/scripts/check_utf8.py
```

Use mock models and test sources for routine checks. Do not make real provider calls just to run the unit tests.

After changing backend application code, rebuild the local services with `docker compose up -d --build`.

## Frontend development

From the project folder:

```text
cd terminal
npm ci
npm run dev
```

Check the production build before submitting frontend changes:

```text
npm run build
```

The build includes TypeScript checking. Keep the lockfile with dependency changes; do not commit `node_modules/`, `dist/`, or `.vite/` caches.

## Documentation changes

Keep the landing README focused on the product and the first step. Put setup commands in the installation guide, interface behavior in the user guide, and internals in the technical guides.

Check that relative links and images resolve, commands state their working directory, and Windows instructions work in PowerShell. Describe behavior supported by the current application, especially settings, demo modes, and key handling.

Screenshots belong in `assets/`. Keep runtime strategy documents under `candle_agent/prompts/`.

## Pull requests

Explain the problem, the resulting behavior, and the checks performed. Keep unrelated changes separate. If a change affects setup or user-visible behavior, update the relevant guide in the same PR.

CI runs Python linting and tests, a UTF-8 check, and a Docker build/startup check. Report any validation you could not run. Never include credentials, local databases, or private analysis records.
