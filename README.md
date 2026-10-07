# DCF Model — Phase 1 / 1B: Data Connectivity

This repository contains the development foundation for a future interactive
discounted cash flow (DCF) application for publicly traded energy companies,
starting with upstream / exploration and production (E&P) companies.

Phase 0 created the development environment. Phase 1 adds a centralized FMP
client and an EOG connectivity check. Phase 1B adds a separate WRDS access probe
to determine whether my WRDS account can supply historical
financial data. FMP remains in place. This is an explicitly authorized feasibility
check, not a permanent change to the master specification's provider hierarchy.
The research notebook now adds annual financial normalization and an optional
SEC company-facts adapter. There is no database persistence, valuation model,
web server, or frontend.

Project instructions are in `CODEX_INSTRUCTIONS.md`. The authoritative master
specification is `MASTER-SPEC-—-DCF-Application.txt`, as designated by the user.
Ignore `MASTER_SPEC.md`; no Markdown duplicate is needed.

## What is installed

- Python 3.14.7 was used to create and verify this environment.
- `.venv` is a virtual environment: a private Python environment for this folder,
  so project packages do not need to be installed globally.
- `pytest` runs automated checks without a real API key or internet access.
- `python-dotenv` reads the local FMP `.env` configuration. These two packages
  are the direct dependencies in `requirements.txt`; pip installs their support
  packages. FMP HTTP requests use Python's built-in `urllib` library.
- WRDS uses a separate `.venv-wrds` environment with Python 3.12.14 and
  `requirements-wrds.txt`. The official `wrds==3.5.0` package requires
  `pandas>=2.2,<2.3`; installation of that pandas version failed under Python
  3.14, so the existing FMP environment was preserved. WRDS also installs its
  required NumPy, SQLAlchemy, and PostgreSQL client libraries; no local database
  server is installed or designed.
- SQLite is already included with Python. No database installation is needed.
- Git tracks changes locally once you make commits. Initializing Git does not
  upload files or create a GitHub repository.

The historical research pipeline is described below. FastAPI, Uvicorn, HTML,
CSS, JavaScript, and valuation work remain deferred.

## Explore EOG financials in Jupyter

Open `notebooks/EOG_Dynamic.ipynb` for a configurable rolling annual window,
standardized WRDS or SEC financials, source audits, ratios, and debt/NWC
diagnostics. The prior root-level `EOG_Financials.ipynb` remains a static research
snapshot. FMP returned HTTP 402 for the configured annual statement endpoint.
Existing E&P observations are joined by year; new operating disclosures are
not automatically retrieved and future missing values remain blank.

From PowerShell, install the notebook dependencies into the existing project
environment and launch JupyterLab:

```powershell
Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab
```

In JupyterLab, open `notebooks/EOG_Dynamic.ipynb` and choose **Run All**.
It defaults to the newest saved WRDS report for the selected ticker. Refresh
expanded inputs with `.\.venv-wrds\Scripts\python.exe -m app.refresh_wrds --ticker EOG --years 6`,
complete the WRDS sign-in/Duo prompts, and rerun the notebook. Six records
support five displayed years and an opening year for changes. For SEC setup,
source definitions, and remaining automation limits, see `notebooks/README.md`.

## Folder structure

```text
DCF-Model/
|-- .git/                       Local Git metadata (generated, hidden)
|-- .venv/                      Isolated Python environment (generated, ignored)
|-- .venv-wrds/                 Python 3.12 WRDS environment (generated, ignored)
|-- app/
|   |-- __init__.py              Makes app a Python package
|   |-- sanity.py                Tiny environment check
|   |-- fmp.py                   All FMP HTTP, configuration, and raw-file logic
|   |-- verify_fmp.py            Runs the five live EOG requests
|   |-- wrds_client.py           Centralized WRDS authentication/discovery/queries
|   `-- verify_wrds.py           Interactive account and EOG access check
|-- tests/
|   |-- test_environment.py      In-memory SQLite verification
|   |-- test_fmp.py              Offline FMP protocol and security tests
|   `-- test_wrds.py             Offline WRDS discovery and raw-query tests
|-- data/raw/fmp/EOG/            Created on successful retrieval; ignored by Git
|-- data/raw/wrds/               Created after account inspection; ignored by Git
|-- .env                        Your local API key (ignored by Git)
|-- .env.example                Safe configuration example; no credentials
|-- .gitignore                  Keeps secrets and generated files out of Git
|-- CODEX_INSTRUCTIONS.md        Project implementation instructions
|-- MASTER-SPEC-—-DCF-Application.txt  Supplied master specification
|-- README.md                   These instructions
|-- requirements.txt            FMP/test dependencies and tested versions
`-- requirements-wrds.txt       Adds WRDS dependencies for Python 3.12
```

Running Python and pytest may create `__pycache__` and `.pytest_cache` folders.
They are normal, ignored generated files. `.git` and `.venv` are managed by their
tools; you do not need to edit their contents.

`app` is the future home for application code and `tests` is the home for checks.
More folders will be added when real features need them, rather than creating
empty API, model, database, or frontend scaffolding now.

## Run the checks on Windows

Setup has already been completed in this folder. Nothing needs to be installed
again to use the existing environment.

1. Open the Windows Start menu, type **PowerShell**, and open it.
2. Copy this command, paste it into PowerShell, and press **Enter**:

   ```powershell
   Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
   ```

3. Run each of the following commands separately, pressing **Enter** after each:

   ```powershell
   .\.venv\Scripts\python.exe -m app.sanity
   .\.venv\Scripts\python.exe -m pytest -q
   .\.venv\Scripts\python.exe -m pip check
   ```

Expected results:

- The sanity check prints Python and SQLite versions, the interpreter path inside
  this folder's `.venv`, and a success message.
- pytest reports all tests passed. The SQLite test uses an in-memory table.
  FMP tests use explicitly synthetic responses and temporary files; they do not
  retrieve or manufacture real financial data and do not consume API requests.
- pip reports `No broken requirements found.`

The long Python path explicitly selects this project's environment. You do not
need to activate it or change PowerShell's execution policy. `-m` tells Python
to run the named module or tool.

## Recreate the environment only if needed

Use these commands from the project folder for a fresh checkout or if `.venv`
is missing. Do not copy a virtual environment between computers. Install Python
and Git first if working on a different computer; this setup was tested with
Python 3.14.7.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The installation command needs access to the Python Package Index (PyPI).
Then run the three checks above. `requirements.txt` pins direct dependencies;
it is not a complete lockfile for every support package.

## Configure FMP locally

1. Sign in to your FMP account and copy your own key from the
   [FMP dashboard](https://site.financialmodelingprep.com/developer/docs/dashboard).
2. From PowerShell in the project folder, run:

   ```powershell
   notepad .env
   ```

3. The local `.env` file uses an `FMP_API_KEY=` entry. If the key is already
   configured, leave it unchanged. Otherwise paste it immediately after the
   equals sign, press **Ctrl+S**, and close
   Notepad. Do not put the key in `.env.example`, a terminal command, or chat.
   If setting up a fresh copy and `.env` is absent, first run:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Verify that Git ignores it:

   ```powershell
   git check-ignore .env
   ```

   Expected: `.env`. If nothing prints, stop and restore the `.env` rule in
   `.gitignore` before adding files to Git.

The client reads only the project-root `.env` file, independently of the working
directory. Shell environment variables do not override that file. It supports
UTF-8 files (including a Windows byte-order mark) and does not expand `${...}`
inside the key. No key is loaded when merely importing the module.

## Run the live EOG check

From the project folder, run:

```powershell
.\.venv\Scripts\python.exe -m app.verify_fmp
```

This makes five sequential requests, one each for profile, latest available
quote, annual income statements, annual balance sheets, and annual cash flow
statements. Each statement request asks for up to five annual records. This is
a retrieval limit, not a decision about the eventual model's historical period.
FMP may return fewer records depending on coverage and account access.

Expected: five `PASS` lines, a small sample of original provider fields, saved
file paths, a local invalid-ticker rejection, and `Live EOG checks: 5/5 passed`.
Missing fields are displayed as unavailable; JSON `null` remains `null`.
The quote timestamp is FMP's Unix timestamp in seconds, not the retrieval time.
The first returned record is shown without reordering or normalizing records.

The command exits unsuccessfully if any check fails. It attempts all five EOG
requests so partial account access is visible. There are no automatic retries;
each request has a 20-second network timeout. The invalid-ticker check rejects
malformed input locally and does not consume an API request. Empty responses
for unknown tickers are covered separately in the offline tests.

Common failures and what to do:

| Message | Next action |
| --- | --- |
| Missing `FMP_API_KEY` | Open `.env`, fill in the key, save, and rerun. Ensure the file is named `.env`, not `.env.txt`. |
| HTTP 401 | Check the key against your FMP dashboard. |
| HTTP 402 or 403 | Check your FMP account's access to the endpoint and EOG; do not assume a paid upgrade is necessary without checking. |
| HTTP 429 | Wait for your account's request limit to reset before rerunning. |
| HTTP 5xx | FMP could not serve the request; try again later. |
| Network failure or timeout | Check internet/firewall access to FMP and rerun. |
| Empty response | FMP supplied no records; this can mean an unknown ticker or unavailable data, not zero financial values. |
| Malformed JSON, unexpected ticker, or nonannual period | Do not use the result; investigate provider behavior before changing assumptions. |
| Cannot save raw files | Check that the project folder is writable. |

If Python says a module is missing, run
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and retry.

## Client and raw responses

### Live verification status — September 30, 2026

The saved local key authenticated successfully for EOG's company profile.
FMP returned `EOG Resources, Inc.`, currency `USD`, and industry
`Oil & Gas Exploration & Production`. The original response and retrieval
metadata are saved in `data/raw/fmp/EOG/`.

| Requested EOG data | Live result |
| --- | --- |
| Company profile | Passed; one record saved |
| Quote | HTTP 402; subscription access restriction |
| Annual income statements | HTTP 402 |
| Annual balance sheets | HTTP 402 |
| Annual cash flow statements | HTTP 402 |

FMP's inspected explanations for the quote and income statement identify the
requested `symbol` as unavailable under the current subscription. Retrying the
income statement with `limit=1` produced the same restriction, so the five-record
request limit did not cause that failure. No alternate ticker or provider was
substituted, and the account or subscription was not changed.

The verification command's output was checked against the actual configured
key before being displayed; the key was absent. Invalid ticker rejection also
passed. Phase 1 remains partially verified until all five EOG endpoints pass.
Review account access with FMP before deciding whether any subscription change
is appropriate; do not replace the key merely because an endpoint returned 402.

### Client interface

All FMP requests are centralized in `app/fmp.py`. The client methods are:

| Method | Stable endpoint |
| --- | --- |
| `get_profile("EOG")` | `/profile` |
| `get_quote("EOG")` | `/quote` |
| `get_income_statement("EOG")` | `/income-statement`, `period=annual` |
| `get_balance_sheet("EOG")` | `/balance-sheet-statement`, `period=annual` |
| `get_cash_flow("EOG")` | `/cash-flow-statement`, `period=annual` |

Endpoint references: [FMP quickstart](https://site.financialmodelingprep.com/developer/docs/quickstart),
[income statement](https://site.financialmodelingprep.com/developer/docs/stable/income-statement),
[balance sheet](https://site.financialmodelingprep.com/developer/docs/stable/balance-sheet-statement),
[cash flow](https://site.financialmodelingprep.com/developer/docs/stable/cashflow-statement).

Each successful call returns an `FMPResponse` containing `records`, `raw_path`,
and `metadata_path`. `records` retains provider names, values, nulls, absent
fields, and record order. There are no financial calculations or conversions.
Only basic response-shape, symbol, and annual-period checks are performed.

Successful raw JSON response bodies are saved byte-for-byte under
`data/raw/fmp/EOG/`. Each has a separate `.metadata.json` file with provider,
endpoint without credentials, request parameters without the key, and UTC
retrieval time. Original reporting dates, currencies, and other provider fields
remain in the raw response. New downloads receive unique filenames so earlier
responses are preserved. These are inspection files, not database persistence.

Error bodies and malformed or empty responses are not archived. Request URLs
and provider error text are never printed because they can contain credentials.
A successful-looking response that echoes the key is rejected instead of saved.
HTTPS certificate validation remains enabled and redirects are refused.

## Configuration and Git

`.env.example` is a blank template, not the file the application loads. The
`.gitignore` rules exclude `.env`, local `.env.*` variants, `.venv`, caches,
raw downloads, and local database files. The safe `.env.example` is deliberately
allowed in Git. Ignoring a file prevents normal Git tracking; it does not
encrypt it or control OneDrive synchronization of this project folder.

To inspect the repository, run:

```powershell
git status --short --branch
git check-ignore .env .env.local .venv/
git remote -v
```

Git starts on the `main` branch with no commits. `??` means a file or folder
exists but has not been added to Git's history yet; that is expected at this
stage. The ignore check should list all three paths. `git remote -v` should
print nothing because no remote hosting destination is configured.

No initial commit, GitHub repository, or publication is required for Phase 0.
Nothing has been uploaded, and no Git name or email configuration was changed.

## Phase 1B — WRDS account discovery and EOG sample

### Live verification completed — September 30, 2026

The user authenticated through the standard WRDS workflow. The account exposed
**216 libraries**, with no errors while listing relevant table names. Full
Compustat North America annual data was accessible through **`comp.funda`**.
EOG matched **GVKEY `016478`**, company name **`EOG RESOURCES INC`**. Five annual
records were retrieved for fiscal years **2021–2025**, currency **USD**.
Every candidate column listed below was present in this table.

Relevant visible libraries included:

- Compustat: `comp`, `comp_na_daily_all`, `comp_global_daily`, `comp_execucomp`.
- CRSP: `crsp`, `crsp_a_stock`, `crsp_q_indexhist`.
- Audit-related: `audit`, `audit_audit_comp`, `audit_common`, `audit_corp_legal`.
- WRDS applications: `wrdsapps`, `wrdsapps_finratio`, and additional linking,
  event-study, and other application libraries listed in the report.
- Sample libraries included `ciqsamp` and its related schemas, `compsamp`,
  `crspsamp`, `zacksamp`, and `tr_sdc_samples`. These names do not establish full
  subscriptions to the corresponding products.
- SEC-related: `secsamp`, `secsamp_all`, and `wrdssec_midas`. The first two expose
  sample-related table names; `wrdssec_midas` exposed a single table, `security`.
  Only schema/table names were inspected; no SEC records were retrieved.

Only the Compustat annual data path was tested with financial record queries;
visibility of other libraries is not proof of every table's read access.

The saved report is:
`data/raw/wrds/20260930T171845404911Z_459115d1/verification.json`.
It contains the full library inventory, table names, original field comments,
five EOG records, exact query parameters, and UTC retrieval timestamp.

Selected **raw, unscaled FY2025** values (not normalized financial-model inputs):

| Raw field | Provider column comment | Returned value |
| --- | --- | --- |
| `revt` | Revenue - Total | `22595.0000` |
| `oiadp` | Operating Income After Depreciation | `6509.0000` |
| `ni` | Net Income (Loss) | `4980.0000` |
| `che` | Cash and Short-Term Investments | `3396.0000` |
| `at` | Assets - Total | `51799.0000` |
| `dlc` | Debt in Current Liabilities - Total | `499.0000` |
| `dltt` | Long-Term Debt - Total | `8636.0000` |
| `ppent` | Property, Plant and Equipment - Total (Net) | `43517.0000` |
| `dp` | Depreciation and Amortization | `4522.0000` |
| `dpc` | Depreciation and Amortization (Cash Flow) | `4571.0000` |
| `capx` | Capital Expenditures | `6594.0000` |
| `oancf` | Operating Activities - Net Cash Flow | `10044.0000` |
| `csho` | Common Shares Outstanding | `537.6700` |
| `cshfd` | Common Shares Used to Calc Earnings Per Share - Fully Diluted | `546.0000` |

Do not sum debt fields or treat cash/share/D&A definitions as interchangeable
until Phase 2 mapping rules and units have been reviewed. Provider column
comments were retrieved; full unit validation and normalization remain outside
Phase 1B. No statement values were scaled or filled with zeros.

The user chose `y` at WRDS's optional password-file prompt. This is a supported
WRDS workflow: on Windows it uses `%APPDATA%\postgresql\pgpass.conf`, outside
this repository. The agent did not read or copy the saved credentials. Git also
ignores common PostgreSQL credential filenames if accidentally placed in the
project. The instructions below recommend `n` for a new session when password
storage is unnecessary; the saved file does not need to be removed to use WRDS.

### Authenticate and run locally

WRDS access is account-specific. A Clemson affiliation alone is not evidence
that this particular account can query Compustat. The program must authenticate,
list the account's libraries, and test access before making that determination.

Open your own PowerShell window and run these commands one at a time:

```powershell
Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
.\.venv-wrds\Scripts\python.exe -m app.verify_wrds
```

If PowerShell has trouble with the relative path, use this equivalent full-path
command from the project folder. The leading `&` tells PowerShell to run the
quoted executable path:

```powershell
& "C:\Users\cadec\OneDrive\Documents\DCF-Model\.venv-wrds\Scripts\python.exe" -m app.verify_wrds
```

At the standard WRDS prompts:

1. Enter your **WRDS account username**. This may differ from your Windows or
   Clemson username. Do not assume the default shown in brackets is correct.
2. Enter your WRDS password. No characters appear while you type it; that is
   normal. Press **Enter** when done.
3. Approve the **Duo Mobile push** on your enrolled device. WRDS PostgreSQL
   access uses Duo push, not a typed Duo passcode.
4. If asked `Create .pgpass file now [y/n]?:`, enter **n**. This avoids saving
   the password. An existing standard PostgreSQL password file may be used by
   WRDS automatically, but this project does not create one itself.

Never put WRDS credentials in source, `.env`, a shell command, chat, or an
inspection report. The local authentication prompts come from the official
WRDS library; the password is hidden. Keep `verbose=False`. Do not send terminal
screenshots containing your login details.

References: [official WRDS Python package](https://github.com/wharton/wrds),
[WRDS Duo instructions](https://wrds-www.wharton.upenn.edu/pages/about/log-in-to-wrds-using-two-factor-authentication/).

Expected output includes authentication status, library count, relevant library
names, SEC-related schema candidates, Compustat status, the chosen table, and
up to two displayed EOG records. Up to five historical records are saved. Tell
Codex **Finished** when the command ends; Codex can read the saved report without
receiving your credentials. The automated agent does not attempt interactive
sign-in on your behalf.

### What gets inspected and saved

All WRDS access lives in `app/wrds_client.py`, through the official `wrds`
package. Remote connections use SSL and are set read-only. Queries have a
30-second statement timeout; authentication has a 30-second connection timeout.
No local database is created.

- The full account-visible library list is saved. A name-based shortlist covers
  Compustat, CRSP, Capital IQ, FactSet, Worldscope, Refinitiv, Audit Analytics,
  Zacks, WRDS applications, and SEC-related names when present. This shortlist is
  a discovery aid, not proof of particular dataset contents or subscriptions.
- Relevant table names are inspected. SEC inspection stops at schema and table
  names; no filings or SEC records are retrieved.
- The program looks for an accessible North America `funda` annual fundamentals
  table, prioritizing `comp`, `comp_na_daily`, and `compa` when actually listed.
  Sample/global/bank libraries remain visible in the report but are not treated
  as proof of full North America access.
- It inspects provider column metadata and verifies read access. It resolves
  `EOG` to a unique `gvkey` from a ticker field, using the same schema's `company`
  table if necessary. An ambiguous identity stops retrieval rather than picking
  a company silently.
- The annual query uses `indfmt=INDL`, `datafmt=STD`, `consol=C`, and `popsrc=D`.
  These select industrial-format, standardized, consolidated domestic records.
  They are recorded as retrieval filters; they do not normalize financial values.
  Records are ordered by `datadate` descending and limited to five.

Candidate raw fields include:

| Purpose to investigate in Phase 2 | Candidate WRDS names |
| --- | --- |
| Company, reporting date, fiscal year, currency | `gvkey`, `tic`, `conm`, `datadate`, `fyear`, `fyr`, `curcd` |
| Revenue and operating income | `sale`, `revt`, `oiadp`, `oibdp` |
| Net income, cash, total assets | `ni`, `che`, `at` |
| Debt components and PP&E | `dlc`, `dltt`, `ppent` |
| Depreciation/amortization candidates | `dp`, `dpc` |
| Capital expenditures and operating cash flow | `capx`, `oancf` |
| Share-count candidates | `csho`, `cshfd` |

Only columns actually present are selected. Unavailable columns are listed
separately. Missing values stay missing; no totals, substitutions, scaling,
ratios, or financial calculations are performed. Similar candidate names are
not treated as interchangeable. In particular, share-count definitions and
financial units must be checked before Phase 2 mapping.

Each completed inspection saves one unique
`data/raw/wrds/<retrieval-time>_<id>/verification.json` with the inventory, access
results, source table, provider column metadata, query/filter parameters, and
raw field names and values. These files are ignored by Git. Exact decimal
values are represented as strings in JSON to avoid rounding; dates are strings
and SQL missing values are `null`. This is a raw query export, not a company
schema or database. No credentials or connection strings are saved.

Possible results:

| Status | Meaning |
| --- | --- |
| `available` | A candidate North America annual table was readable and EOG rows returned. |
| `available_no_eog` | Table access worked but the EOG lookup/annual selection returned no rows. |
| `not_available_in_account_catalogue` | No suitable full North America annual table was exposed/readable in this account; consult the saved alternatives. |
| `undetermined` | A query, catalogue, metadata, or identity check failed. This is not evidence of no subscription. |

An authentication failure leaves availability unknown. An inaccessible Compustat
table does not trigger replacement data ingestion; alternative library/table
names are reported for review only. Catalog errors are retained as safe generic
messages rather than raw driver errors.

### Verify the WRDS environment

```powershell
.\.venv-wrds\Scripts\python.exe -m pytest -q
.\.venv-wrds\Scripts\python.exe -m pip check
```

Expected: all tests pass and no broken requirements. Tests use synthetic mocked
responses, never credentials or live WRDS queries. Running tests in the original
`.venv` still checks FMP and SQLite; the WRDS test module is intentionally skipped
there because WRDS is installed only in `.venv-wrds`.

The local environment was created with the existing Python 3.12.14 bundled at
`C:\Users\cadec\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
For a fresh setup on another computer with Python 3.12 installed, run:

```powershell
py -3.12 -m venv .venv-wrds
.\.venv-wrds\Scripts\python.exe -m pip install -r requirements-wrds.txt
```

Do not copy virtual environments between machines. No runtime reinstall is
needed on this computer.

### Troubleshooting

| Problem | What to do |
| --- | --- |
| `No module named wrds` | Use `.venv-wrds\Scripts\python.exe`, not the original `.venv` Python. |
| PowerShell says module `..venv-wrds` could not be loaded | The relative path lost a backslash. Copy the full-path command above, including its leading `&`. |
| Sign-in fails | Verify your WRDS account works on the WRDS website, then retry locally. Do not paste the password into chat. |
| No Duo push or timeout | Check Duo enrollment/notifications and approve the push promptly; consult WRDS support if it still fails. |
| Network failure | Check access to `wrds-pgdata.wharton.upenn.edu`, TCP port `9737`; consult campus IT/WRDS support about network restrictions. |
| Compustat unavailable | Review actual alternative library names in the saved report and ask Clemson library/WRDS support about account entitlements. |
| Query/catalogue check unresolved | Share only the generic error and saved report path; access remains undetermined until reviewed. |

## Recommended next task (not started)

Phase 1B has established access to EOG annual fundamentals. Request **Phase 2 —
define the minimal provider-independent company/financial schema and explicit
Compustat `comp.funda` field mappings**, verifying units and preserving source
identifiers, periods, currency, and missing values, with mapping tests. Retain
FMP's existing profile capability and explicitly document the provider roles
before changing permanent specification rules. Phase 2, database design, DCF
calculations, and frontend work have not been started.
