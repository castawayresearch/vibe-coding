# SysMonitor

A beautiful, snappy terminal GUI for monitoring Linux systems — built with [Textual](https://textual.textualize.io/) and Python.

```
📊 Overview  |  ⚙️ Script Runner  |  📋 History  |  🌐 HTTP Tester  |  🔧 Admin
```

---

## Features

| Tab | Feature |
|-----|---------|
| **📊 Overview** | Live system status emoji (💀→🔴→🟠→🟡→🟢→✅) with pass-rate bar and per-check results |
| **⚙️ Script Runner** | Run a configurable bash check script; streaming output with PASS/FAIL colour coding; results saved to disk |
| **📋 History** | Scrollable table of the last 100 script runs (date, time, pass/fail count, duration); persisted to JSON |
| **🌐 HTTP Tester** | Full endpoint tester — HTTP/HTTPS, any method, headers, JSON/form body, basic auth, client cert (PEM), CA cert (CRT), port override, follow-redirects toggle, curl command generation |
| **🔧 Admin** | Configure all file paths; manage HTTP examples (add/edit/delete/save/reload); portable config via CLI or env var |

---

## Requirements

- Python 3.11+
- Linux (runs without root)
- Network access for HTTP Tester

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/sysmonitor.git
cd sysmonitor

# 2. Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Make the check script executable
chmod +x checks.sh
```

---

## Running

### Default (config at `~/.sysmonitor/config.json`)

```bash
python monitor.py
```

### Portable — custom config file

Pass any config file on the command line.  Useful for sharing a config across machines or running multiple isolated instances:

```bash
python monitor.py --config /path/to/my-config.json
# or short form
python monitor.py -c /path/to/my-config.json
```

### Portable — environment variable

```bash
export SYSMONITOR_CONFIG=/shared/configs/prod-config.json
python monitor.py
```

Priority order: `--config` argument > `SYSMONITOR_CONFIG` env var > default `~/.sysmonitor/config.json`.

The active config source is shown in the **Admin → File Paths** section.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` | Overview tab |
| `2` | Script Runner tab |
| `3` | History tab |
| `4` | HTTP Tester tab |
| `5` | Admin tab |
| `r` | Run checks (from any tab) |
| `q` | Quit |

---

## Tab Reference

### 📊 Overview

Displays the aggregate system health as a single large emoji:

| Emoji | Meaning | Pass rate |
|-------|---------|-----------|
| ✅ | All Systems Operational | 100% |
| 🟢 | Systems Healthy | ≥ 80% |
| 🟡 | Minor Issues Detected | ≥ 60% |
| 🟠 | Significant Issues | ≥ 40% |
| 🔴 | Critical Problems | ≥ 20% |
| 💀 | System Critical | < 20% |

A colour-coded progress bar and individual check results are shown alongside.

---

### ⚙️ Script Runner

Executes a configurable bash script and streams its output live.

**Protocol — how the script communicates results:**

```bash
# Write results in this format:
echo "Check Name: PASS"   # or FAIL
echo "Check Name: PASS" >> "$RESULTS_FILE"   # app sets this env var

# Prefix lines for colour coding:
echo "[CHECK] My check: PASS"   # cyan
echo "[INFO]  Some info"        # dimmed
echo "Anything else"            # plain
```

The app sets `RESULTS_FILE` in the script's environment pointing to the configured results path.  The script writes one `Name: PASS` or `Name: FAIL` line per check.

See [`checks.sh`](checks.sh) for a complete working example with 10 system checks.

---

### 📋 History

Each time the script runner completes, a record is appended to the history file:

```json
{
  "timestamp": "2026-03-04T14:32:10.123456",
  "passed": 9,
  "total": 10,
  "all_pass": false,
  "duration": 4.2
}
```

The last 100 runs are kept.  Use the **Refresh** button or switch tabs to reload.

---

### 🌐 HTTP Tester

Test any HTTP/HTTPS endpoint without leaving the terminal.

**Supported options:**

| Field | Description |
|-------|-------------|
| Method | GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS |
| URL | Full URL including scheme |
| Port override | Force a specific port (rewrites the URL's host) |
| Timeout | Request timeout in seconds |
| Follow redirects | Toggle `-L` behaviour |
| Headers | Pipe-separated: `Authorization: Bearer x \| X-Foo: bar` |
| Body | JSON string or URL-encoded form data |
| Content-Type | Auto-injects header; choices include `application/json`, `application/x-www-form-urlencoded`, etc. |
| Username / Password | HTTP Basic Auth |
| Client cert (.pem) | Path to PEM file (mTLS) |
| Client key | Path to private key (mTLS) |
| CA / server cert | Path to CA bundle or server certificate (.crt) |
| Verify SSL | Enable/disable server certificate verification (`-k`) |

**Load example** — choose a pre-built request from the dropdown and click **Load ↓**.

**Show curl** — generates the equivalent `curl` command you can paste into a terminal.

The response panel shows:
- Redirect chain (if any)
- HTTP status (colour-coded)
- Request headers sent
- Response headers
- Response body (JSON pretty-printed automatically)
- Timing, size, and TLS indicator

---

### 🔧 Admin

#### File Paths

All file paths used by the application are configurable here.  Changes take effect immediately and are persisted to the active config file.

| Field | Default | Purpose |
|-------|---------|---------|
| Config file | `~/.sysmonitor/config.json` | Read-only; set via `--config` or env var |
| Results file | `~/.sysmonitor/check_results.txt` | Latest PASS/FAIL results |
| History file | `~/.sysmonitor/run_history.json` | Script run history |
| Script file | `./checks.sh` | Bash script executed by Script Runner |
| Examples file | `~/.sysmonitor/examples.json` | Custom HTTP examples for Tab 4 |

#### HTTP Examples

Manage the examples shown in the Tab 4 dropdown:

| Button | Action |
|--------|--------|
| 📥 Load → Form | Populate the form from the selected row |
| ➕ Add / Update | Save the form as a new or updated example (live update, Tab 4 refreshes) |
| 🗑 Delete Selected | Remove the highlighted example |
| 💾 Save to File | Write all examples to the configured examples file |
| 🔄 Reload from File | Read examples from disk (discards unsaved edits) |
| ↩️ Reset to Defaults | Restore the 10 built-in examples |

---

## Configuration File

The config file is plain JSON.  You can edit it by hand or use the Admin tab.

```json
{
  "results_file": "/home/user/.sysmonitor/check_results.txt",
  "history_file": "/home/user/.sysmonitor/run_history.json",
  "script_file":  "/opt/scripts/checks.sh",
  "examples_file": "/home/user/.sysmonitor/examples.json"
}
```

All paths support `~` expansion.

---

## HTTP Examples File

The examples file is a JSON array.  Copy [`examples/examples.json`](examples/examples.json) as a starting point:

```json
[
  {
    "name": "My API health check",
    "method": "GET",
    "url": "https://api.example.com/health",
    "headers": "Authorization: Bearer mytoken | Accept: application/json",
    "body": "",
    "content_type": "none",
    "follow_redirects": "yes",
    "username": "",
    "password": ""
  }
]
```

Point the **Examples file** path in Admin to your file, then click **Reload from File**.

---

## Customising `checks.sh`

Edit `checks.sh` to add your own checks.  The only requirement is that each check writes a result line to `$RESULTS_FILE`:

```bash
#!/usr/bin/env bash
RESULTS_FILE="${RESULTS_FILE:-$HOME/.sysmonitor/check_results.txt}"
> "$RESULTS_FILE"   # clear previous results

pass() { echo "$1: PASS"; echo "$1: PASS" >> "$RESULTS_FILE"; }
fail() { echo "$1: FAIL"; echo "$1: FAIL" >> "$RESULTS_FILE"; }

# --- Your checks ---
if systemctl is-active --quiet myservice; then
    pass "My Service"
else
    fail "My Service"
fi

if curl -sf https://my-api.example.com/health > /dev/null; then
    pass "API Health"
else
    fail "API Health"
fi
```

---

## Portability — Running on Multiple Machines

Because all state is in files and the config path is configurable, SysMonitor is easy to use across machines:

```bash
# Machine A — generate a config
python monitor.py --config /shared/nfs/sysmonitor-prod.json

# Machine B — use the same config from a shared drive
python monitor.py --config /shared/nfs/sysmonitor-prod.json

# Or set it once in your shell profile
echo 'export SYSMONITOR_CONFIG=/etc/sysmonitor/config.json' >> ~/.bashrc
```

The config file itself just contains file paths — update those paths to match each machine's layout.

---

## Project Structure

```
sysmonitor/
├── README.md              # This file
├── LICENSE                # MIT
├── requirements.txt       # Python dependencies
├── .gitignore
├── monitor.py             # Main application (single file)
├── checks.sh              # Default bash check script
└── examples/
    └── examples.json      # Sample HTTP examples for Tab 4
```

Data files written at runtime (not checked in):

```
~/.sysmonitor/
├── config.json            # Active configuration
├── check_results.txt      # Latest check pass/fail results
├── run_history.json       # Script run history (last 100)
└── examples.json          # Custom HTTP examples (if saved from Admin)
```

---

## Troubleshooting

### App won't start — `ModuleNotFoundError: No module named 'textual'`

```bash
pip install -r requirements.txt
```

If you have multiple Python versions:
```bash
python3 -m pip install -r requirements.txt
```

### Script Runner shows "Script not found"

- Check the **Script file** path in the Admin tab (Tab 5).
- Make sure the script is executable: `chmod +x checks.sh`
- Use an absolute path if the working directory differs.

### HTTP Tester — SSL Error

- For self-signed certificates: set **Verify SSL** to `No (-k)`.
- For custom CA: enter the path to your `.crt` file in **CA / server cert**.
- For mutual TLS: provide both **Client cert (.pem)** and **Client key**.

### HTTP Tester — Connection Error

- Verify the URL is reachable: `curl -v <url>` in another terminal.
- Check for a port mismatch — use **Port override** if the service is on a non-standard port.
- Confirm no firewall is blocking the connection.

### History tab is empty after running checks

- The Script Runner must complete before history is written.
- Check that `history_file` in Admin points to a writable path.
- Click **Refresh** in the History tab.

### Examples dropdown is empty in HTTP Tester

- Go to Admin (Tab 5) → HTTP Examples → click **Reset to Defaults**.
- Or point the **Examples file** to `examples/examples.json` and click **Reload from File**.

### Config changes in Admin don't seem to take effect

- File path changes (results, history, script, examples) take effect immediately for new operations but do not move existing files.
- The Script Runner reads the script path each time **Run Checks** is clicked.
- If you changed the history file path, existing history at the old path is not migrated automatically.

### `checks.sh` results not appearing in Overview

- Ensure every check line is written to `$RESULTS_FILE` (the env var set by the app).
- The format must be exactly: `Check Name: PASS` or `Check Name: FAIL` (case-insensitive).
- Lines that don't match the pattern are displayed as plain log output and not counted.

### Textual rendering issues in some terminals

Textual requires a terminal with:
- 256-colour or true-colour support
- Unicode support

```bash
# Test colour support
echo $TERM        # should be xterm-256color or similar
echo $COLORTERM   # truecolor is ideal
```

Set `TERM=xterm-256color` if needed.

---

## License

[MIT](LICENSE)
