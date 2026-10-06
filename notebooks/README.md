# EOG financials notebook

`EOG_Financials.ipynb` builds a five-year historical table and selected
historical ratios from the EOG annual report retrieved through WRDS and
Compustat. It labels reported and calculated fields, leaves unavailable items
blank, and includes an E&P operating-data checklist. It deliberately stops
before forecasting and DCF. FMP returned HTTP 402 for the annual statement
endpoint with the configured account. A WRDS report containing five annual EOG
records for FY2021–FY2025 is already saved under
`data/raw/wrds/20260930T171845404911Z_459115d1/`.

## Start JupyterLab on Windows

Open PowerShell and run:

```powershell
Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab
```

The first command installs JupyterLab and notebook dependencies into this
project's existing Python environment. The second starts JupyterLab; open
`notebooks/EOG_Financials.ipynb` from its file browser and select **Run All**.
The notebook reads the newest saved WRDS report and displays the annual records
and provider field descriptions. It does not connect to WRDS or handle your
password inside the notebook.

## Refresh the WRDS report

In a separate PowerShell window, run:

```powershell
Set-Location 'C:\Users\cadec\OneDrive\Documents\DCF-Model'
.\.venv-wrds\Scripts\python.exe -m app.verify_wrds
```

Sign in at the WRDS prompts and approve the Duo push. The program saves a new
raw report under `data/raw/wrds/`. Return to Jupyter and rerun the report-loading
cell to read the newest report. Keep credentials out of notebook cells.
