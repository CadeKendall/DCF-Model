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
- `FINANCIAL_SOURCE = "sec"`: cached SEC XBRL facts. Set `"wrds"` for the saved WRDS extract. No silent provider substitution.
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

## Forward model: Sections 12-16

Sections 12-16 build the forecast and valuation on top of the historical sections:

- **12** forward assumptions table: nine reported months of 2026 (Q3 earnings release tables), verified Q4/FY2026 guidance, the EIA STEO price deck and the editable selections in `data/assumptions/EOG_assumptions.json`.
- **13** FY2026E-FY2031E operating forecast and unlevered free cash flow.
- **14** WACC (10-year Treasury, Damodaran industry beta and implied ERP, EOG's cost of debt).
- **15** perpetuity and exit-multiple terminal values, enterprise value, equity bridge, implied share price.
- **16** Bear/Base/Bull/Custom scenarios, sensitivity grids and model warnings.

Edit assumptions in `data/assumptions/EOG_assumptions.json`, then rerun Sections 12-16. Each driver is either a number with a written rationale or a named rule (guidance midpoint, STEO price, five-year historical median). `"status": "Decision needed"` stops the forecast until a value is chosen.

Filed guidance is in `data/reference/eog_guidance.json`. EOG printed its Q4/FY2026 guidance table as an image: those values were transcribed manually and the code checks the image's SHA-256, not the numbers. Quoted 10-Q sentences are matched word-for-word in the cached filing.

New refresh flags in Section 12 (all `False` for cached runs; set `True` once for new data):

- `REFRESH_GUIDANCE`: guidance exhibits/images, quarterly supplemental tables and the 10-Q (SEC cache).
- `REFRESH_EIA`: EIA STEO workbook (`data/raw/eia/`, no API key).
- `REFRESH_MARKET`: FMP price snapshot (`/profile`; the `/quote` endpoint returns HTTP 402 on the current plan), Treasury yield curve and Damodaran datasets (`data/raw/market/`).

Install the two new notebook dependencies once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
```

## Automation limits

Section 11 extracts tax and cash-flow disclosures and rebuilds CFO from net
income with explicit sign conversions. It separates current/deferred/other tax
expense from cash taxes; disclosed tax-credit purchases are memo items already
included in cash taxes in the reviewed source.

The existing capex field is the sum of the property-addition lines before the
separately presented investing working-capital contribution. Section 11 displays
both bases, acquisitions, asset sales and other investing payments, and rebuilds
investing cash flow from every disclosed line. Unknown new investing lines stop
that reconciliation. Absent categories remain blank, not invented zeros.

`REFRESH_CASHFLOW_FILINGS = False` uses saved documents. Refresh SEC metadata
before setting this to `True` once for a new window, then return it to `False`.
Working-capital views retain their signs and different scopes. CFO less property
spending is a historical diagnostic, not DCF UFCF. The next forward-assumptions
table must choose operating taxes, reinvestment and NWC definitions explicitly.

Section 10 retrieves historical operating-cost lines from the same annual filing
selection as revenue. It reconciles detailed expenses, operating income (the
model's EBIT basis), and calculated EBITDA = operating income + DD&A. Older
separate gathering/processing and transportation lines are combined only where
the combined GP&T line is unavailable; the audit records the derivation.

`REFRESH_COST_FILINGS = False` uses saved documents. Refresh SEC metadata before
using `True` once for a new window, then return it to `False`. Costs per Boe use
production calculated from the filing's rounded daily commodity volumes and
actual days in each year. These are historical diagnostics, not automatically
accepted forecast rates. EBITDA retains impairments and other noncash items;
it is not adjusted EBITDA or cash flow. Taxes, capex and operating working-capital
definitions still need review before the cash-flow forecast.

Section 9 builds the historical oil/condensate, gas and NGL revenue model from
daily production times days in the year times realized price, with explicit unit
conversion and leap-year handling. Its separate `revenue_disclosures` table
automatically extracts production and composite realized prices from supported
EOG annual filing tables, plus reported sales and other revenue components from
inline XBRL. It does not overwrite the earlier operating reference dataset.

`REFRESH_REVENUE_FILINGS = False` uses saved documents. After refreshing SEC
metadata, set it to `True` once to download documents for a new window, then return
it to `False`. The loader uses comparative years to reduce required downloads.
All components for a year come from one selected filing; a comparison with the
separate SEC annual revenue series exposes total-revenue differences.

The model retains calculated-versus-reported sales differences, estimates a
bound from published rounding precision, and separately reconciles reported
upstream sales plus marketing, derivatives, disposition gains/losses and other
items to total revenue. Marketing costs remain visible. These are historical
reconstructions, not future production or price assumptions.

Section 8 retrieves the selected fiscal year's annual filing document and extracts
EOG acquisition disclosures from inline XBRL, including transaction dimensions.
It builds an acquisition cash/NWC bridge, a candidate G&A expense adjustment,
and a table of acquired contributions and unaudited pro forma disclosures.
Amounts and dates come from filing tags, not notebook constants.

`REFRESH_FILING = False` reads the separately saved HTML document. Set it to `True`
once when a newly selected annual filing is not cached, then return it to `False`.
Refresh SEC metadata first to discover new filings. Cached runs need no network.
This adapter supports the observed EOG disclosure structure; unsupported mappings
remain unavailable. The bridge does not finalize operating NWC or estimate organic
production. Allocations are as disclosed in the annual report, may be preliminary,
and do not incorporate subsequent quarterly measurement-period revisions.

Financial year selection, normalization, ratios, debt/NWC diagnostics, source comparison,
and descriptive trends are dynamic. WRDS refresh requires interactive sign-in; SEC refresh
runs from the notebook after local configuration.

Existing operating observations are preserved in `data/reference/eog_operating_history.json`
by ticker/year/metric, with units, definitions, and source links. New production, price,
reserve, and WTI observations are **not automatically downloaded into that earlier
reference table**. Its future years stay blank until new reviewed records are added.
Section 9 independently retrieves the six commodity production/price inputs for
its revenue model. Section 10 retrieves operating expenses and calculates cost
intensities independently; reserves and other unsupported metrics still need
separate source work.

Debt scope is provider-specific. NWC is an AR + inventory - AP proxy; acquisition balances,
capital-related payables, and omitted current accounts need review before UFCF.
Forecast and DCF calculations are in Sections 12-16, described above.
