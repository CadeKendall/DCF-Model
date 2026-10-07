# Dynamic financial research notebook

Open `notebooks/EOG_Dynamic.ipynb` and choose **Run > Run All Cells**.
The root-level `EOG_Financials.ipynb` remains your prior static research notebook.

## Start Jupyter

In PowerShell:

```powershell
Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab
```

## Settings in the first cell

- `TICKER = "EOG"`: company to load.
- `HISTORY_YEARS = 5`: displayed annual periods.
- `END_YEAR = None`: end at the newest annual period in the source; set a year for an earlier window.
- `FINANCIAL_SOURCE = "wrds"`: saved WRDS extract. Set `"sec"` for standard SEC XBRL facts. No silent provider substitution.
- `SEC_CIK = "0000821189"`: EOG's identifier; change with ticker for SEC.
- `REFRESH_SEC = False`: use cache. True downloads a new SEC snapshot.

The window advances when new annual data are retrieved, not when the calendar changes.
One preceding year is retained internally for financial growth and NWC changes.
Missing years, fields, and zero denominators stay blank. The audit table records sources and formulas.

## Refresh WRDS

The original extract lacks receivables, inventory, payables, taxes, pretax income, and other
newly requested fields. Retrieve six periods for a five-year display and opening balances:

```powershell
.\.venv-wrds\Scripts\python.exe -m app.refresh_wrds --ticker EOG --years 6
```

Use the WRDS login prompts and approve Duo. No credentials go in notebook cells.
The command saves a new raw report. Rerun the notebook to normalize it.
The original `python -m app.verify_wrds` access-check command still works.

## Configure SEC locally

Append this setting to your existing `.env`, replacing the contact text with your email:

```text
SEC_USER_AGENT=DCF-Model your-contact-email
```

Do not overwrite the existing file or its FMP key. `.env.example` documents this setting.
Set `FINANCIAL_SOURCE = "sec"` and `REFRESH_SEC = True` for the first download;
set refresh back to False for cached exploration. No SEC API key is needed.
The request includes the configured contact header. Failed refreshes are shown instead
of silently substituting an older snapshot. Ticker/CIK identity is checked.

SEC normalization uses actual start/end dates, annual durations, and 10-K or 10-K/A disclosures.
A filing's `fy` label is not used to label comparative facts. The latest available disclosure
per concept/period is selected and its filing reference retained. This is a latest-disclosure
research view, not a point-in-time backtest. SEC labels years by fiscal-period-end year;
Compustat uses its own fiscal-year field. Non-calendar fiscal calendars need care in comparisons.
Only explicit standard concepts are mapped. Custom tags and absent concepts remain unavailable.
See the [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## Automation limits

Financial year selection, normalization, ratios, debt/NWC diagnostics, source comparison,
and descriptive trends are dynamic. WRDS refresh requires interactive sign-in; SEC refresh
runs from the notebook after local configuration.

Existing operating observations are preserved in `data/reference/eog_operating_history.json`
by ticker/year/metric, with units, definitions, and source links. New production, price,
reserve, and WTI observations are **not automatically downloaded**. Future years stay blank
until new reviewed records are added or a dedicated source adapter is implemented.
Moving those values out of code does not automate their retrieval.

Debt scope is provider-specific. NWC is an AR + inventory - AP proxy; acquisition balances,
capital-related payables, and omitted current accounts need review before UFCF.
There are no forecast or DCF calculations.
