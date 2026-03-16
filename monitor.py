#!/usr/bin/env python3
"""
SysMonitor - Terminal system monitoring application
Requires: pip install textual requests
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests import Session
from requests.exceptions import RequestException

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

# ---------------------------------------------------------------------------
# Configuration  (single bootstrap constant; everything else lives in config)
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".sysmonitor"
DATA_DIR.mkdir(exist_ok=True)

# How the config file was resolved — shown in the Admin pane.
_CONFIG_SOURCE: str = "default (~/.sysmonitor/config.json)"


def _resolve_config_file() -> Path:
    """Determine config file path with this priority order:
      1. --config / -c  CLI argument
      2. SYSMONITOR_CONFIG environment variable
      3. Default: ~/.sysmonitor/config.json

    Using parse_known_args so Textual's own arguments are not consumed.
    """
    global _CONFIG_SOURCE
    parser = argparse.ArgumentParser(
        prog="monitor.py",
        description="SysMonitor — terminal system monitor",
        add_help=False,
    )
    parser.add_argument(
        "--config", "-c",
        metavar="PATH",
        default=None,
        help="Path to config.json  (overrides SYSMONITOR_CONFIG env var)",
    )
    args, _ = parser.parse_known_args()

    if args.config:
        p = Path(args.config).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_SOURCE = f"--config argument  ({p})"
        return p

    env_val = os.environ.get("SYSMONITOR_CONFIG", "").strip()
    if env_val:
        p = Path(env_val).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_SOURCE = f"SYSMONITOR_CONFIG env var  ({p})"
        return p

    _CONFIG_SOURCE = f"default  ({DATA_DIR / 'config.json'})"
    return DATA_DIR / "config.json"


CONFIG_FILE: Path = _resolve_config_file()

_DEFAULT_CONFIG: dict = {
    "results_file":        str(DATA_DIR / "check_results.txt"),
    "history_file":        str(DATA_DIR / "run_history.json"),
    "script_file":         str(Path(__file__).parent / "checks.sh"),
    "examples_file":       str(DATA_DIR / "examples.json"),
    "s3_enabled":          False,
    "s3_region":           "us-east-1",
    "s3_bucket":           "",
    "s3_key_prefix":       "sysmonitor",
    "s3_access_key_id":    "",
    "s3_secret_access_key": "",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**_DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# Mutable global — Admin pane updates this at runtime.
APP_CONFIG: dict = load_config()


def cfg_path(key: str) -> Path:
    """Return the configured path for a given key, with ~ expansion."""
    return Path(APP_CONFIG[key]).expanduser()


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def get_status_emoji(pass_rate: float) -> tuple[str, str]:
    """Return (emoji, description) for a given pass rate 0.0–1.0."""
    if pass_rate == 1.0:
        return "✅", "All Systems Operational"
    elif pass_rate >= 0.8:
        return "🟢", "Systems Healthy"
    elif pass_rate >= 0.6:
        return "🟡", "Minor Issues Detected"
    elif pass_rate >= 0.4:
        return "🟠", "Significant Issues"
    elif pass_rate >= 0.2:
        return "🔴", "Critical Problems"
    else:
        return "💀", "System Critical"


def load_history() -> list[dict]:
    hf = cfg_path("history_file")
    if not hf.exists():
        return []
    try:
        return json.loads(hf.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_history(history: list[dict]) -> None:
    hf = cfg_path("history_file")
    hf.parent.mkdir(parents=True, exist_ok=True)
    hf.write_text(json.dumps(history, indent=2))
    try:
        upload_history_to_s3(history)
    except Exception:
        pass  # S3 is optional; never block local save


def upload_history_to_s3(history: list[dict]) -> None:
    """Upload history JSON to S3 if S3 backup is enabled in config."""
    if not APP_CONFIG.get("s3_enabled"):
        return
    bucket = APP_CONFIG.get("s3_bucket", "").strip()
    if not bucket:
        return
    import boto3
    prefix = APP_CONFIG.get("s3_key_prefix", "sysmonitor").strip().rstrip("/")
    region = APP_CONFIG.get("s3_region", "us-east-1").strip()
    key_id = APP_CONFIG.get("s3_access_key_id", "").strip()
    secret = APP_CONFIG.get("s3_secret_access_key", "").strip()
    kwargs: dict = {"region_name": region}
    if key_id and secret:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
    s3 = boto3.client("s3", **kwargs)
    s3_key = f"{prefix}/run_history.json"
    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=json.dumps(history, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def load_results() -> list[dict]:
    """Read results_file; each line is 'CheckName: PASS' or 'CheckName: FAIL'."""
    rf = cfg_path("results_file")
    if not rf.exists():
        return []
    results: list[dict] = []
    try:
        for line in rf.read_text().splitlines():
            line = line.strip()
            if ": " in line:
                name, _, status = line.partition(": ")
                results.append({"name": name.strip(), "status": status.strip()})
    except OSError:
        pass
    return results


def build_curl_command(
    url: str,
    method: str,
    headers: dict[str, str],
    body: str,
    client_cert: str,
    client_key: str,
    ca_cert: str,
    verify_ssl: bool,
    follow_redirects: bool = True,
    username: str = "",
    password: str = "",
) -> str:
    parts = ["curl", "-s", "-X", method]
    if follow_redirects:
        parts.append("-L")
    if not verify_ssl:
        parts.append("-k")
    if username or password:
        parts += ["-u", shlex.quote(f"{username}:{password}")]
    if client_cert and client_key:
        parts += ["--cert", shlex.quote(f"{client_cert}:{client_key}")]
    elif client_cert:
        parts += ["--cert", shlex.quote(client_cert)]
    if ca_cert:
        parts += ["--cacert", shlex.quote(ca_cert)]
    for k, v in headers.items():
        parts += ["-H", shlex.quote(f"{k}: {v}")]
    if body:
        parts += ["-d", shlex.quote(body)]
    parts.append(shlex.quote(url))
    return " \\\n  ".join(parts)


# Pre-built request examples loaded into the HTTP tester form.
EXAMPLES: dict[str, dict] = {
    "Google HTTPS GET": {
        "method": "GET",
        "url": "https://www.google.com",
        "headers": "Accept: text/html,application/xhtml+xml",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "Google HTTP → HTTPS redirect": {
        "method": "GET",
        "url": "http://www.google.com",
        "headers": "Accept: text/html",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin GET (JSON response)": {
        "method": "GET",
        "url": "https://httpbin.org/get",
        "headers": "Accept: application/json | X-Tool: SysMonitor",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin POST JSON body": {
        "method": "POST",
        "url": "https://httpbin.org/post",
        "headers": "Accept: application/json",
        "body": '{"message": "hello", "tool": "SysMonitor"}',
        "content_type": "application/json",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin POST form-urlencoded": {
        "method": "POST",
        "url": "https://httpbin.org/post",
        "headers": "Accept: application/json",
        "body": "field1=hello&field2=world",
        "content_type": "application/x-www-form-urlencoded",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin Basic Auth": {
        "method": "GET",
        "url": "https://httpbin.org/basic-auth/user/pass",
        "headers": "",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "user",
        "password": "pass",
    },
    "httpbin Response Headers": {
        "method": "GET",
        "url": "https://httpbin.org/response-headers?X-Custom=hello&Server=SysMonitor",
        "headers": "Accept: application/json",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin 404 error": {
        "method": "GET",
        "url": "https://httpbin.org/status/404",
        "headers": "",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin PUT JSON": {
        "method": "PUT",
        "url": "https://httpbin.org/put",
        "headers": "Accept: application/json",
        "body": '{"id": 42, "name": "updated"}',
        "content_type": "application/json",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
    "httpbin DELETE": {
        "method": "DELETE",
        "url": "https://httpbin.org/delete",
        "headers": "Accept: application/json",
        "body": "",
        "content_type": "none",
        "follow_redirects": "yes",
        "username": "",
        "password": "",
    },
}

# Keep a reference to the hardcoded defaults so Admin can reset.
_BUILTIN_EXAMPLES: dict[str, dict] = dict(EXAMPLES)


def load_examples_from_file() -> dict[str, dict]:
    """Load examples from configured JSON file; fall back to built-ins."""
    ex_file = cfg_path("examples_file")
    if ex_file.exists():
        try:
            raw = json.loads(ex_file.read_text())
            if isinstance(raw, list):
                loaded = {
                    ex["name"]: {k: v for k, v in ex.items() if k != "name"}
                    for ex in raw
                    if isinstance(ex, dict) and "name" in ex and "url" in ex
                }
                if loaded:
                    return loaded
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return dict(_BUILTIN_EXAMPLES)


def save_examples_to_file(examples: dict[str, dict]) -> None:
    """Persist the examples dict to the configured examples_file."""
    ex_file = cfg_path("examples_file")
    ex_file.parent.mkdir(parents=True, exist_ok=True)
    data = [{"name": name, **vals} for name, vals in examples.items()]
    ex_file.write_text(json.dumps(data, indent=2))


# Mutable global — Admin pane can update and reload this at runtime.
ACTIVE_EXAMPLES: dict[str, dict] = load_examples_from_file()


# ---------------------------------------------------------------------------
# Overview / Status pane
# ---------------------------------------------------------------------------

class StatusDisplay(Static):
    """Large centred emoji + status text."""

    pass_rate: reactive[float] = reactive(-1.0)

    DEFAULT_CSS = """
    StatusDisplay {
        content-align: center middle;
        text-align: center;
        height: 1fr;
        border: round $accent;
        padding: 2 4;
    }
    """

    def render(self) -> str:
        if self.pass_rate < 0:
            return (
                "[bold dim]No data yet.[/bold dim]\n\n"
                "[dim]Run the script from the Script Runner tab\n"
                "to populate status.[/dim]"
            )
        emoji, desc = get_status_emoji(self.pass_rate)
        pct = int(self.pass_rate * 100)
        bar_filled = int(self.pass_rate * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        color = (
            "green" if self.pass_rate >= 0.8
            else "yellow" if self.pass_rate >= 0.6
            else "red"
        )
        return (
            f"[bold]{emoji}[/bold]\n\n"
            f"[bold white]{desc}[/bold white]\n\n"
            f"[{color}]{bar}[/{color}]\n\n"
            f"[bold {color}]{pct}%[/bold {color}] [dim]checks passing[/dim]"
        )


class ResultsList(Static):
    DEFAULT_CSS = """
    ResultsList {
        height: 1fr;
        border: round $primary;
        padding: 1 2;
        overflow-y: auto;
    }
    """


class StatusPane(Container):
    DEFAULT_CSS = """
    StatusPane { height: 100%; }
    StatusPane Horizontal { height: 1fr; }
    StatusPane .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield StatusDisplay(id="status-display")
            with VerticalScroll():
                yield Label("Latest Check Results", classes="section-title")
                yield ResultsList(id="results-list")

    def refresh_data(self) -> None:
        results = load_results()
        display = self.query_one("#status-display", StatusDisplay)
        results_widget = self.query_one("#results-list", ResultsList)

        if not results:
            display.pass_rate = -1.0
            results_widget.update("[dim]No results yet.[/dim]")
            return

        passed = sum(1 for r in results if r["status"].upper() == "PASS")
        total = len(results)
        display.pass_rate = passed / total if total else 0.0

        lines = []
        for r in results:
            icon = "✅" if r["status"].upper() == "PASS" else "❌"
            col = "green" if r["status"].upper() == "PASS" else "red"
            lines.append(f"{icon} [{col}]{r['name']}[/{col}]")
        results_widget.update("\n".join(lines))


# ---------------------------------------------------------------------------
# Script Runner pane
# ---------------------------------------------------------------------------

class ScriptPane(Container):
    DEFAULT_CSS = """
    ScriptPane { height: 100%; }
    ScriptPane .controls {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-bottom: 1;
    }
    ScriptPane .controls Horizontal { height: auto; margin-bottom: 0; }
    ScriptPane Button { margin-right: 1; }
    ScriptPane Input { width: 1fr; margin-right: 1; }
    ScriptPane RichLog {
        height: 1fr;
        border: round $primary;
    }
    ScriptPane .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="controls"):
            yield Label("Checks Script Runner", classes="section-title")
            with Horizontal():
                yield Input(value=str(cfg_path("script_file")), id="script-path")
                yield Button("▶  Run Checks", id="run-btn", variant="primary")
                yield Button("🗑  Clear", id="clear-btn")
        yield RichLog(id="script-log", highlight=True, markup=True, wrap=True)

    @on(Button.Pressed, "#clear-btn")
    def clear_log(self) -> None:
        self.query_one("#script-log", RichLog).clear()

    @on(Button.Pressed, "#run-btn")
    def start_run(self) -> None:
        btn = self.query_one("#run-btn", Button)
        btn.disabled = True
        btn.label = "⏳ Running…"
        self._run_script()

    @work(exclusive=True)
    async def _run_script(self) -> None:
        log = self.query_one("#script-log", RichLog)
        btn = self.query_one("#run-btn", Button)
        script_path = self.query_one("#script-path", Input).value.strip()

        divider = "[bold cyan]" + "─" * 60 + "[/bold cyan]"
        log.write(divider)
        log.write(f"[bold]▶  {script_path}[/bold]")
        log.write(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
        log.write(divider)

        if not Path(script_path).exists():
            log.write(f"[bold red]ERROR: Script not found: {script_path}[/bold red]")
            btn.disabled = False
            btn.label = "▶  Run Checks"
            return

        start = datetime.now()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "RESULTS_FILE": str(cfg_path("results_file"))},
            )
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if ": PASS" in line or line.endswith("PASS"):
                    log.write(f"[green]✅  {line}[/green]")
                elif ": FAIL" in line or line.endswith("FAIL"):
                    log.write(f"[red]❌  {line}[/red]")
                elif line.startswith("[INFO]"):
                    log.write(f"[dim]{line}[/dim]")
                elif line.startswith("[CHECK]"):
                    log.write(f"[cyan]{line}[/cyan]")
                else:
                    log.write(line)
            await proc.wait()
        except Exception as exc:
            log.write(f"[bold red]Execution error: {exc}[/bold red]")
            btn.disabled = False
            btn.label = "▶  Run Checks"
            return

        duration = (datetime.now() - start).total_seconds()
        results = load_results()
        passed = sum(1 for r in results if r["status"].upper() == "PASS")
        total = len(results)

        log.write(divider)
        log.write(
            f"[bold]{'✅' if passed == total else '❌'}  "
            f"{passed}/{total} passed  •  {duration:.1f}s[/bold]"
        )
        log.write(divider)

        # Persist run to history
        history = load_history()
        history.insert(0, {
            "timestamp": start.isoformat(),
            "passed": passed,
            "total": total,
            "all_pass": passed == total and total > 0,
            "duration": round(duration, 1),
        })
        save_history(history[:100])

        self.app.refresh_all_panes()
        btn.disabled = False
        btn.label = "▶  Run Checks"


# ---------------------------------------------------------------------------
# History pane
# ---------------------------------------------------------------------------

class HistoryPane(Container):
    DEFAULT_CSS = """
    HistoryPane { height: 100%; }
    HistoryPane .controls {
        height: auto;
        padding: 1 2;
        margin-bottom: 1;
    }
    HistoryPane .controls Horizontal { height: auto; }
    HistoryPane DataTable {
        height: 1fr;
        border: round $primary;
    }
    HistoryPane .section-title {
        text-style: bold;
        color: $accent;
    }
    HistoryPane Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="controls"):
            with Horizontal():
                yield Label("Run History  (last 100 runs)", classes="section-title")
                yield Button("🔄  Refresh", id="refresh-btn")
        yield DataTable(id="history-table", zebra_stripes=True)

    def on_mount(self) -> None:
        tbl = self.query_one("#history-table", DataTable)
        tbl.add_columns("Date", "Time", "Passed", "Total", "All Pass?", "Duration (s)")
        self.load_history()

    @on(Button.Pressed, "#refresh-btn")
    def refresh_history(self) -> None:
        self.load_history()

    def load_history(self) -> None:
        tbl = self.query_one("#history-table", DataTable)
        tbl.clear()
        for run in load_history():
            ts = datetime.fromisoformat(run["timestamp"])
            all_pass = "✅ Yes" if run.get("all_pass") else "❌ No"
            tbl.add_row(
                ts.strftime("%Y-%m-%d"),
                ts.strftime("%H:%M:%S"),
                str(run.get("passed", "?")),
                str(run.get("total", "?")),
                all_pass,
                str(run.get("duration", "?")),
            )


# ---------------------------------------------------------------------------
# HTTP Endpoint Tester pane
# ---------------------------------------------------------------------------

class HttpTesterPane(Container):
    DEFAULT_CSS = """
    HttpTesterPane { height: 100%; }

    HttpTesterPane .form-area {
        height: auto;
        max-height: 60%;
        border: round $primary;
        padding: 1 2;
        margin-bottom: 1;
    }
    HttpTesterPane .row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }
    HttpTesterPane .lbl {
        width: 16;
        color: $accent;
        text-style: bold;
    }
    HttpTesterPane .lbl-sm {
        width: 14;
        color: $accent;
    }
    HttpTesterPane .input-wide  { width: 1fr; }
    HttpTesterPane .input-short { width: 16; margin-right: 2; }
    HttpTesterPane .input-mid   { width: 1fr; margin-right: 2; }

    HttpTesterPane Select { width: 22; margin-right: 2; }
    HttpTesterPane Select.method-sel { width: 16; }
    HttpTesterPane Select.example-sel { width: 1fr; }

    HttpTesterPane .example-bar {
        height: auto;
        margin-bottom: 1;
        border-bottom: dashed $primary;
        padding-bottom: 1;
        align: left middle;
    }
    HttpTesterPane .example-bar Label {
        width: 16;
        color: $accent;
        text-style: bold;
    }
    HttpTesterPane .example-bar Button { margin-left: 1; }

    HttpTesterPane .btn-row {
        height: auto;
        margin-top: 1;
    }
    HttpTesterPane Button { margin-right: 1; }

    HttpTesterPane .response-area {
        height: 1fr;
        border: round $primary;
    }
    HttpTesterPane RichLog { height: 1fr; }

    HttpTesterPane .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    HttpTesterPane .hint {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        example_opts = [(name, name) for name in ACTIVE_EXAMPLES]

        with VerticalScroll(classes="form-area"):
            yield Label("🌐  HTTP Endpoint Tester", classes="section-title")
            yield Label(
                "Headers: pipe-separated  e.g.  Authorization: Bearer x | X-Foo: bar",
                classes="hint",
            )

            # ── Example loader ──────────────────────────────────────────────
            with Horizontal(classes="example-bar"):
                yield Label("Load example")
                yield Select(example_opts, value=example_opts[0][1],
                             id="example-select", classes="example-sel")
                yield Button("Load ↓", id="load-example-btn", variant="default")

            # ── Method + URL ────────────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Method", classes="lbl")
                yield Select(
                    [("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"),
                     ("PATCH", "PATCH"), ("DELETE", "DELETE"),
                     ("HEAD", "HEAD"), ("OPTIONS", "OPTIONS")],
                    value="GET", id="method-select", classes="method-sel",
                )
                yield Label("URL", classes="lbl-sm")
                yield Input(placeholder="https://example.com/api/v1/status",
                            id="url-input", classes="input-wide")

            # ── Port override + Timeout ─────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Port override", classes="lbl")
                yield Input(placeholder="8443 (optional)",
                            id="port-input", classes="input-short")
                yield Label("Timeout (s)", classes="lbl-sm")
                yield Input(value="15", id="timeout-input", classes="input-short")
                yield Label("Follow redirects", classes="lbl-sm")
                yield Select(
                    [("Yes  -L", "yes"), ("No", "no")],
                    value="yes", id="follow-redirects-select",
                )

            # ── Headers ─────────────────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Headers", classes="lbl")
                yield Input(
                    placeholder="Authorization: Bearer token | X-Custom: value",
                    id="headers-input", classes="input-wide",
                )

            # ── Body + Content-Type ─────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Body", classes="lbl")
                yield Input(
                    placeholder='{"key": "value"}  or  param=value&other=x',
                    id="body-input", classes="input-wide",
                )

            with Horizontal(classes="row"):
                yield Label("Content-Type", classes="lbl")
                yield Select(
                    [("(none / keep headers)", "none"),
                     ("application/json", "application/json"),
                     ("application/x-www-form-urlencoded",
                      "application/x-www-form-urlencoded"),
                     ("text/plain", "text/plain"),
                     ("text/html", "text/html"),
                     ("multipart/form-data", "multipart/form-data")],
                    value="none", id="content-type-select",
                )

            # ── Basic Auth ──────────────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Username", classes="lbl")
                yield Input(placeholder="(basic auth, optional)",
                            id="username-input", classes="input-mid")
                yield Label("Password", classes="lbl-sm")
                yield Input(placeholder="(basic auth, optional)",
                            id="password-input", password=True,
                            classes="input-mid")

            # ── Client cert + key ───────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("Client cert (.pem)", classes="lbl")
                yield Input(placeholder="/path/to/client.pem",
                            id="client-cert-input", classes="input-mid")
                yield Label("Client key", classes="lbl-sm")
                yield Input(placeholder="/path/to/client.key",
                            id="client-key-input", classes="input-mid")

            # ── CA cert + SSL verify ────────────────────────────────────────
            with Horizontal(classes="row"):
                yield Label("CA / server cert", classes="lbl")
                yield Input(placeholder="/path/to/ca.crt  (optional)",
                            id="ca-cert-input", classes="input-mid")
                yield Label("Verify SSL", classes="lbl-sm")
                yield Select(
                    [("Yes (default)", "yes"), ("No  -k", "no")],
                    value="yes", id="verify-ssl-select",
                )

            # ── Action buttons ──────────────────────────────────────────────
            with Horizontal(classes="btn-row"):
                yield Button("🚀  Send Request", id="send-btn", variant="primary")
                yield Button("📋  Show curl", id="curl-btn", variant="success")
                yield Button("🗑  Clear output", id="clear-resp-btn")

        with Container(classes="response-area"):
            yield RichLog(id="response-log", highlight=True, markup=True, wrap=True)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _str(self, widget_id: str, cls=Input) -> str:
        """Read a widget value as a clean string; handles Select.BLANK."""
        w = self.query_one(widget_id, cls)
        v = w.value
        return str(v) if v and v is not Select.BLANK else ""

    def _set(self, widget_id: str, value: str, cls=Input) -> None:
        """Set a widget value."""
        self.query_one(widget_id, cls).value = value

    def _form_values(self) -> dict:
        url = self._str("#url-input")
        port_str = self._str("#port-input")
        if port_str:
            try:
                parsed = urlparse(url)
                url = urlunparse(parsed._replace(
                    netloc=f"{parsed.hostname}:{int(port_str)}"
                ))
            except (ValueError, Exception):
                pass

        # Parse pipe-separated headers
        headers: dict[str, str] = {}
        for chunk in self._str("#headers-input").split("|"):
            chunk = chunk.strip()
            if ":" in chunk:
                k, _, hv = chunk.partition(":")
                headers[k.strip()] = hv.strip()

        # Inject Content-Type if selected and not already present
        ct = self._str("#content-type-select", Select)
        if ct and ct != "none":
            headers.setdefault("Content-Type", ct)

        try:
            timeout = float(self._str("#timeout-input") or "15")
        except ValueError:
            timeout = 15.0

        return {
            "method": self._str("#method-select", Select) or "GET",
            "url": url,
            "headers": headers,
            "body": self._str("#body-input"),
            "client_cert": self._str("#client-cert-input"),
            "client_key": self._str("#client-key-input"),
            "ca_cert": self._str("#ca-cert-input"),
            "verify_ssl": self._str("#verify-ssl-select", Select) != "no",
            "follow_redirects": self._str("#follow-redirects-select", Select) != "no",
            "username": self._str("#username-input"),
            "password": self._str("#password-input"),
            "timeout": timeout,
        }

    def _make_curl(self, v: dict) -> str:
        return build_curl_command(
            url=v["url"],
            method=v["method"],
            headers=v["headers"],
            body=v["body"],
            client_cert=v["client_cert"],
            client_key=v["client_key"],
            ca_cert=v["ca_cert"],
            verify_ssl=v["verify_ssl"],
            follow_redirects=v["follow_redirects"],
            username=v["username"],
            password=v["password"],
        )

    # -----------------------------------------------------------------------
    # Example loader
    # -----------------------------------------------------------------------

    def reload_examples(self) -> None:
        """Rebuild the examples Select from the current ACTIVE_EXAMPLES global."""
        sel = self.query_one("#example-select", Select)
        opts = [(name, name) for name in ACTIVE_EXAMPLES]
        sel.set_options(opts)
        if opts:
            sel.value = opts[0][1]

    @on(Button.Pressed, "#load-example-btn")
    def load_example(self) -> None:
        name = self._str("#example-select", Select)
        ex = ACTIVE_EXAMPLES.get(name)
        if not ex:
            return
        self._set("#method-select", ex.get("method", "GET"), Select)
        self._set("#url-input", ex.get("url", ""))
        self._set("#headers-input", ex.get("headers", ""))
        self._set("#body-input", ex.get("body", ""))
        self._set("#content-type-select", ex.get("content_type", "none"), Select)
        self._set("#follow-redirects-select", ex.get("follow_redirects", "yes"), Select)
        self._set("#username-input", ex.get("username", ""))
        self._set("#password-input", ex.get("password", ""))
        self._set("#client-cert-input", "")
        self._set("#client-key-input", "")
        self._set("#ca-cert-input", "")
        self._set("#verify-ssl-select", "yes", Select)
        self._set("#port-input", "")
        log = self.query_one("#response-log", RichLog)
        log.write(f"[dim]Loaded example: [bold]{name}[/bold][/dim]")

    # -----------------------------------------------------------------------
    # Button handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#clear-resp-btn")
    def clear_response(self) -> None:
        self.query_one("#response-log", RichLog).clear()

    @on(Button.Pressed, "#curl-btn")
    def show_curl(self) -> None:
        log = self.query_one("#response-log", RichLog)
        try:
            v = self._form_values()
            curl = self._make_curl(v)
            log.write("[bold cyan]─── Generated curl command ────────────────────────────[/bold cyan]")
            log.write(f"[bold yellow]{curl}[/bold yellow]")
            log.write("[bold cyan]───────────────────────────────────────────────────────[/bold cyan]\n")
        except Exception as exc:
            log.write(f"[red]Error generating curl: {exc}[/red]")

    @on(Button.Pressed, "#send-btn")
    def send_request(self) -> None:
        self._do_request()

    @work(thread=True)
    def _do_request(self) -> None:
        log = self.query_one("#response-log", RichLog)

        try:
            v = self._form_values()
        except Exception as exc:
            log.write(f"[red]Form error: {exc}[/red]")
            return

        if not v["url"]:
            log.write("[red]Please enter a URL.[/red]")
            return

        curl = self._make_curl(v)
        divider = "[bold cyan]" + "─" * 60 + "[/bold cyan]"
        log.write(divider)
        log.write(f"[bold]{v['method']}  {v['url']}[/bold]")
        log.write(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
        log.write(f"[dim]curl equivalent:[/dim]")
        log.write(f"[dim yellow]{curl}[/dim yellow]")
        log.write(divider)

        try:
            session = Session()

            # SSL verification / CA bundle
            verify: bool | str = v["verify_ssl"]
            if v["ca_cert"] and v["verify_ssl"]:
                verify = v["ca_cert"]

            # Client certificate
            cert: Optional[tuple[str, str] | str] = None
            if v["client_cert"] and v["client_key"]:
                cert = (v["client_cert"], v["client_key"])
            elif v["client_cert"]:
                cert = v["client_cert"]

            # Basic auth
            auth = (v["username"], v["password"]) if v["username"] or v["password"] else None

            resp = session.request(
                method=v["method"],
                url=v["url"],
                headers=v["headers"],
                data=v["body"].encode() if v["body"] else None,
                cert=cert,
                verify=verify,
                allow_redirects=v["follow_redirects"],
                auth=auth,
                timeout=v["timeout"],
            )

            # ── Redirect chain ───────────────────────────────────────────
            if resp.history:
                log.write(f"[dim]↳ Redirect chain ({len(resp.history)} hop(s)):[/dim]")
                for redir in resp.history:
                    log.write(
                        f"[dim]   {redir.status_code} "
                        f"{redir.headers.get('Location', redir.url)}[/dim]"
                    )

            # ── Status ───────────────────────────────────────────────────
            sc = resp.status_code
            color = "green" if resp.ok else ("yellow" if sc < 500 else "red")
            log.write(f"\n[bold {color}]HTTP {sc}  {resp.reason}[/bold {color}]")

            # ── Request headers sent ─────────────────────────────────────
            log.write("\n[bold]Request Headers Sent:[/bold]")
            for k, hv in resp.request.headers.items():
                log.write(f"[dim]  {k}: {hv}[/dim]")

            # ── Response headers ─────────────────────────────────────────
            log.write("\n[bold]Response Headers:[/bold]")
            for k, hv in resp.headers.items():
                log.write(f"[dim]  {k}: {hv}[/dim]")

            # ── Response body ────────────────────────────────────────────
            log.write("\n[bold]Response Body:[/bold]")
            if v["method"].upper() == "HEAD":
                log.write("[dim](HEAD request — no body)[/dim]")
            else:
                content_type = resp.headers.get("Content-Type", "")
                try:
                    if "json" in content_type or resp.text.lstrip().startswith(("{", "[")):
                        body_text = json.dumps(resp.json(), indent=2)
                    else:
                        body_text = resp.text
                except Exception:
                    body_text = resp.text
                if len(body_text) > 10_000:
                    body_text = body_text[:10_000] + "\n[dim]… truncated at 10 000 chars[/dim]"
                log.write(body_text)

            log.write(
                f"\n[dim]⏱  {resp.elapsed.total_seconds():.3f}s  │  "
                f"📦  {len(resp.content):,} bytes  │  "
                f"🔒  {'TLS' if resp.url.startswith('https') else 'plain HTTP'}[/dim]"
            )

        except requests.exceptions.SSLError as exc:
            log.write(f"[bold red]SSL Error:[/bold red] {exc}")
        except requests.exceptions.ConnectionError as exc:
            log.write(f"[bold red]Connection Error:[/bold red] {exc}")
        except requests.exceptions.Timeout:
            log.write(f"[bold red]Timeout[/bold red] after {v['timeout']}s")
        except Exception as exc:
            log.write(f"[bold red]{type(exc).__name__}:[/bold red] {exc}")

        log.write(divider + "\n")


# ---------------------------------------------------------------------------
# Admin pane
# ---------------------------------------------------------------------------

class AdminPane(Container):
    DEFAULT_CSS = """
    AdminPane { height: 100%; }

    AdminPane VerticalScroll { height: 1fr; }

    AdminPane .section {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-bottom: 1;
    }
    AdminPane .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    AdminPane .row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }
    AdminPane .lbl {
        width: 18;
        color: $accent;
    }
    AdminPane .lbl-sm {
        width: 14;
        color: $accent;
    }
    AdminPane .ro-path {
        width: 1fr;
        color: $text-muted;
        text-style: italic;
    }
    AdminPane Input { width: 1fr; }
    AdminPane Select { width: 20; margin-right: 1; }
    AdminPane .input-mid { width: 1fr; margin-right: 1; }
    AdminPane Button { margin-right: 1; }
    AdminPane .btn-row { height: auto; margin-top: 1; }

    AdminPane DataTable {
        height: 12;
        border: round $primary;
        margin-bottom: 1;
    }
    AdminPane .status-bar {
        height: auto;
        color: $success;
        text-style: italic;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            # ── File Paths ──────────────────────────────────────────────────
            with Vertical(classes="section"):
                yield Label("📁  File Paths", classes="section-title")

                with Horizontal(classes="row"):
                    yield Label("Config file", classes="lbl")
                    yield Static(
                        f"{CONFIG_FILE}  [dim](source: {_CONFIG_SOURCE})[/dim]",
                        classes="ro-path",
                    )

                with Horizontal(classes="row"):
                    yield Label("Results file", classes="lbl")
                    yield Input(value=APP_CONFIG["results_file"],
                                id="cfg-results-file")

                with Horizontal(classes="row"):
                    yield Label("History file", classes="lbl")
                    yield Input(value=APP_CONFIG["history_file"],
                                id="cfg-history-file")

                with Horizontal(classes="row"):
                    yield Label("Script file", classes="lbl")
                    yield Input(value=APP_CONFIG["script_file"],
                                id="cfg-script-file")

                with Horizontal(classes="row"):
                    yield Label("Examples file", classes="lbl")
                    yield Input(value=APP_CONFIG["examples_file"],
                                id="cfg-examples-file")

                with Horizontal(classes="btn-row"):
                    yield Button("💾  Save Paths", id="save-paths-btn",
                                 variant="primary")
                    yield Button("↩️  Reset to Defaults", id="reset-paths-btn")

                yield Static("", id="paths-status", classes="status-bar")

            # ── S3 Backup ────────────────────────────────────────────────────
            with Vertical(classes="section"):
                yield Label("☁️  S3 Backup  (history pushed after every run)",
                            classes="section-title")

                with Horizontal(classes="row"):
                    yield Label("Enable S3", classes="lbl")
                    yield Select(
                        [("No", "false"), ("Yes", "true")],
                        value="true" if APP_CONFIG.get("s3_enabled") else "false",
                        id="s3-enabled",
                    )

                with Horizontal(classes="row"):
                    yield Label("AWS Region", classes="lbl")
                    yield Input(value=APP_CONFIG.get("s3_region", "us-east-1"),
                                placeholder="us-east-1", id="s3-region")

                with Horizontal(classes="row"):
                    yield Label("S3 Bucket", classes="lbl")
                    yield Input(value=APP_CONFIG.get("s3_bucket", ""),
                                placeholder="my-bucket-name", id="s3-bucket")

                with Horizontal(classes="row"):
                    yield Label("Key Prefix", classes="lbl")
                    yield Input(value=APP_CONFIG.get("s3_key_prefix", "sysmonitor"),
                                placeholder="sysmonitor", id="s3-prefix")

                with Horizontal(classes="row"):
                    yield Label("Access Key ID", classes="lbl")
                    yield Input(value=APP_CONFIG.get("s3_access_key_id", ""),
                                placeholder="(leave blank for IAM role / env vars)",
                                id="s3-key-id")

                with Horizontal(classes="row"):
                    yield Label("Secret Key", classes="lbl")
                    yield Input(value=APP_CONFIG.get("s3_secret_access_key", ""),
                                placeholder="(leave blank for IAM role / env vars)",
                                id="s3-secret", password=True)

                with Horizontal(classes="btn-row"):
                    yield Button("🔗  Test Connection", id="test-s3-btn")
                    yield Button("💾  Save S3 Config", id="save-s3-btn",
                                 variant="primary")

                yield Static("", id="s3-status", classes="status-bar")

            # ── HTTP Examples ───────────────────────────────────────────────
            with Vertical(classes="section"):
                yield Label("🔗  HTTP Examples  (used in Tab 4 dropdown)",
                            classes="section-title")

                yield DataTable(id="examples-table", zebra_stripes=True)

                with Horizontal(classes="btn-row"):
                    yield Button("📥  Load → Form", id="load-ex-btn")
                    yield Button("➕  Add / Update", id="add-ex-btn",
                                 variant="primary")
                    yield Button("🗑  Delete Selected", id="del-ex-btn",
                                 variant="error")
                    yield Button("💾  Save to File", id="save-ex-btn",
                                 variant="success")
                    yield Button("🔄  Reload from File", id="reload-ex-btn")
                    yield Button("↩️  Reset to Defaults", id="reset-ex-btn")

                yield Static("", id="examples-status", classes="status-bar")

                yield Label("Example form  (fill in and click Add / Update):",
                            classes="section-title")

                with Horizontal(classes="row"):
                    yield Label("Name", classes="lbl")
                    yield Input(placeholder="My API check", id="ex-name",
                                classes="input-mid")
                    yield Label("Method", classes="lbl-sm")
                    yield Select(
                        [("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"),
                         ("PATCH", "PATCH"), ("DELETE", "DELETE"),
                         ("HEAD", "HEAD"), ("OPTIONS", "OPTIONS")],
                        value="GET", id="ex-method",
                    )

                with Horizontal(classes="row"):
                    yield Label("URL", classes="lbl")
                    yield Input(placeholder="https://example.com/api",
                                id="ex-url")

                with Horizontal(classes="row"):
                    yield Label("Headers", classes="lbl")
                    yield Input(
                        placeholder="Key: Value | Key2: Value2",
                        id="ex-headers",
                    )

                with Horizontal(classes="row"):
                    yield Label("Body", classes="lbl")
                    yield Input(placeholder='{"key": "value"}', id="ex-body")

                with Horizontal(classes="row"):
                    yield Label("Content-Type", classes="lbl")
                    yield Select(
                        [("(none)", "none"),
                         ("application/json", "application/json"),
                         ("application/x-www-form-urlencoded",
                          "application/x-www-form-urlencoded"),
                         ("text/plain", "text/plain"),
                         ("text/html", "text/html")],
                        value="none", id="ex-content-type",
                    )
                    yield Label("Follow redirects", classes="lbl-sm")
                    yield Select(
                        [("Yes", "yes"), ("No", "no")],
                        value="yes", id="ex-follow-redirects",
                    )

                with Horizontal(classes="row"):
                    yield Label("Username", classes="lbl")
                    yield Input(placeholder="(basic auth optional)",
                                id="ex-username", classes="input-mid")
                    yield Label("Password", classes="lbl-sm")
                    yield Input(placeholder="(basic auth optional)",
                                id="ex-password", password=True)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _str(self, widget_id: str, cls=Input) -> str:
        w = self.query_one(widget_id, cls)
        v = w.value
        return str(v) if v and v is not Select.BLANK else ""

    def _set(self, widget_id: str, value: str, cls=Input) -> None:
        self.query_one(widget_id, cls).value = value

    def _set_status(self, widget_id: str, msg: str, error: bool = False) -> None:
        color = "red" if error else "green"
        self.query_one(widget_id, Static).update(f"[{color}]{msg}[/{color}]")

    def _rebuild_table(self) -> None:
        tbl = self.query_one("#examples-table", DataTable)
        tbl.clear()
        for name, ex in ACTIVE_EXAMPLES.items():
            tbl.add_row(name, ex.get("method", "GET"), ex.get("url", ""),
                        key=name)

    def on_mount(self) -> None:
        tbl = self.query_one("#examples-table", DataTable)
        tbl.add_columns("Name", "Method", "URL")
        tbl.cursor_type = "row"
        self._rebuild_table()

    # -----------------------------------------------------------------------
    # File paths handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#save-paths-btn")
    def save_paths(self) -> None:
        APP_CONFIG["results_file"] = self._str("#cfg-results-file")
        APP_CONFIG["history_file"] = self._str("#cfg-history-file")
        APP_CONFIG["script_file"]  = self._str("#cfg-script-file")
        APP_CONFIG["examples_file"] = self._str("#cfg-examples-file")
        try:
            save_config(APP_CONFIG)
            self._set_status("#paths-status",
                             f"✅  Saved to {CONFIG_FILE}")
        except Exception as exc:
            self._set_status("#paths-status", f"Error: {exc}", error=True)

    @on(Button.Pressed, "#reset-paths-btn")
    def reset_paths(self) -> None:
        for key, val in _DEFAULT_CONFIG.items():
            APP_CONFIG[key] = val
        self._set("#cfg-results-file", APP_CONFIG["results_file"])
        self._set("#cfg-history-file", APP_CONFIG["history_file"])
        self._set("#cfg-script-file",  APP_CONFIG["script_file"])
        self._set("#cfg-examples-file", APP_CONFIG["examples_file"])
        try:
            save_config(APP_CONFIG)
            self._set_status("#paths-status", "↩️  Reset to defaults and saved.")
        except Exception as exc:
            self._set_status("#paths-status", f"Error: {exc}", error=True)

    # -----------------------------------------------------------------------
    # S3 backup handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#save-s3-btn")
    def save_s3_config(self) -> None:
        APP_CONFIG["s3_enabled"]          = self._str("#s3-enabled", Select) == "true"
        APP_CONFIG["s3_region"]           = self._str("#s3-region")
        APP_CONFIG["s3_bucket"]           = self._str("#s3-bucket")
        APP_CONFIG["s3_key_prefix"]       = self._str("#s3-prefix")
        APP_CONFIG["s3_access_key_id"]    = self._str("#s3-key-id")
        APP_CONFIG["s3_secret_access_key"] = self._str("#s3-secret")
        try:
            save_config(APP_CONFIG)
            status = "✅  S3 config saved"
            if APP_CONFIG["s3_enabled"]:
                status += f"  (uploads to s3://{APP_CONFIG['s3_bucket']}/{APP_CONFIG['s3_key_prefix']}/run_history.json)"
            self._set_status("#s3-status", status)
        except Exception as exc:
            self._set_status("#s3-status", f"Error: {exc}", error=True)

    @on(Button.Pressed, "#test-s3-btn")
    async def test_s3_connection(self) -> None:
        import asyncio
        self._set_status("#s3-status", "⏳  Testing connection…")
        bucket = self._str("#s3-bucket")
        region = self._str("#s3-region") or "us-east-1"
        key_id = self._str("#s3-key-id")
        secret = self._str("#s3-secret")
        if not bucket:
            self._set_status("#s3-status", "S3 Bucket name is required.", error=True)
            return

        auth_method = "explicit keys" if (key_id and secret) else "IAM / env vars"
        conn_str = f"s3://{bucket}  (region: {region}, auth: {auth_method})"

        def _test() -> str:
            import boto3
            kwargs: dict = {"region_name": region}
            if key_id and secret:
                kwargs["aws_access_key_id"] = key_id
                kwargs["aws_secret_access_key"] = secret
            client = boto3.client("s3", **kwargs)
            client.head_bucket(Bucket=bucket)
            return f"✅  Connected — {conn_str}"

        try:
            msg = await asyncio.to_thread(_test)
            self._set_status("#s3-status", msg)
        except Exception as exc:
            full_error = (
                f"Connection failed\n"
                f"  Endpoint : {conn_str}\n"
                f"  Error    : {exc}"
            )
            self._set_status("#s3-status", full_error, error=True)

    # -----------------------------------------------------------------------
    # Examples table handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#load-ex-btn")
    def load_selected_into_form(self) -> None:
        tbl = self.query_one("#examples-table", DataTable)
        if tbl.cursor_row is None:
            return
        row_key = tbl.get_row_at(tbl.cursor_row)[0]  # Name column
        ex = ACTIVE_EXAMPLES.get(str(row_key))
        if not ex:
            return
        self._set("#ex-name", str(row_key))
        self._set("#ex-method", ex.get("method", "GET"), Select)
        self._set("#ex-url", ex.get("url", ""))
        self._set("#ex-headers", ex.get("headers", ""))
        self._set("#ex-body", ex.get("body", ""))
        self._set("#ex-content-type", ex.get("content_type", "none"), Select)
        self._set("#ex-follow-redirects", ex.get("follow_redirects", "yes"), Select)
        self._set("#ex-username", ex.get("username", ""))
        self._set("#ex-password", ex.get("password", ""))

    @on(Button.Pressed, "#add-ex-btn")
    def add_or_update_example(self) -> None:
        global ACTIVE_EXAMPLES
        name = self._str("#ex-name").strip()
        url  = self._str("#ex-url").strip()
        if not name:
            self._set_status("#examples-status", "Name is required.", error=True)
            return
        if not url:
            self._set_status("#examples-status", "URL is required.", error=True)
            return
        ACTIVE_EXAMPLES[name] = {
            "method":           self._str("#ex-method", Select) or "GET",
            "url":              url,
            "headers":          self._str("#ex-headers"),
            "body":             self._str("#ex-body"),
            "content_type":     self._str("#ex-content-type", Select) or "none",
            "follow_redirects": self._str("#ex-follow-redirects", Select) or "yes",
            "username":         self._str("#ex-username"),
            "password":         self._str("#ex-password"),
        }
        self._rebuild_table()
        self._set_status("#examples-status",
                         f"✅  Example '{name}' added / updated (unsaved).")
        self.app.reload_http_examples()

    @on(Button.Pressed, "#del-ex-btn")
    def delete_selected_example(self) -> None:
        global ACTIVE_EXAMPLES
        tbl = self.query_one("#examples-table", DataTable)
        if tbl.cursor_row is None:
            self._set_status("#examples-status",
                             "Select a row first.", error=True)
            return
        name = str(tbl.get_row_at(tbl.cursor_row)[0])
        if name in ACTIVE_EXAMPLES:
            del ACTIVE_EXAMPLES[name]
            self._rebuild_table()
            self._set_status("#examples-status",
                             f"🗑  Deleted '{name}' (unsaved).")
            self.app.reload_http_examples()

    @on(Button.Pressed, "#save-ex-btn")
    def save_examples(self) -> None:
        try:
            save_examples_to_file(ACTIVE_EXAMPLES)
            self._set_status("#examples-status",
                             f"💾  Saved {len(ACTIVE_EXAMPLES)} examples "
                             f"→ {cfg_path('examples_file')}")
        except Exception as exc:
            self._set_status("#examples-status", f"Error: {exc}", error=True)

    @on(Button.Pressed, "#reload-ex-btn")
    def reload_examples_from_file(self) -> None:
        global ACTIVE_EXAMPLES
        ACTIVE_EXAMPLES = load_examples_from_file()
        self._rebuild_table()
        self._set_status("#examples-status",
                         f"🔄  Reloaded {len(ACTIVE_EXAMPLES)} examples "
                         f"from {cfg_path('examples_file')}")
        self.app.reload_http_examples()

    @on(Button.Pressed, "#reset-ex-btn")
    def reset_examples_to_defaults(self) -> None:
        global ACTIVE_EXAMPLES
        ACTIVE_EXAMPLES = dict(_BUILTIN_EXAMPLES)
        self._rebuild_table()
        self._set_status("#examples-status",
                         f"↩️  Reset to {len(ACTIVE_EXAMPLES)} built-in examples (unsaved).")
        self.app.reload_http_examples()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class SysMonitorApp(App):
    TITLE = "SysMonitor"
    CSS = """
    Screen { background: $surface; }
    TabbedContent, TabPane { height: 1fr; }
    .section-title { text-style: bold; color: $accent; margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("1", "switch_tab('tab-overview')", "Overview"),
        Binding("2", "switch_tab('tab-script')", "Script Runner"),
        Binding("3", "switch_tab('tab-history')", "History"),
        Binding("4", "switch_tab('tab-http')", "HTTP Tester"),
        Binding("5", "switch_tab('tab-admin')", "Admin"),
        Binding("r", "run_script", "Run Checks"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("📊  Overview", id="tab-overview"):
                yield StatusPane(id="status-pane")
            with TabPane("⚙️  Script Runner", id="tab-script"):
                yield ScriptPane(id="script-pane")
            with TabPane("📋  History", id="tab-history"):
                yield HistoryPane(id="history-pane")
            with TabPane("🌐  HTTP Tester", id="tab-http"):
                yield HttpTesterPane(id="http-pane")
            with TabPane("🔧  Admin", id="tab-admin"):
                yield AdminPane(id="admin-pane")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_all_panes()

    def refresh_all_panes(self) -> None:
        try:
            self.query_one("#status-pane", StatusPane).refresh_data()
        except Exception:
            pass
        try:
            self.query_one("#history-pane", HistoryPane).load_history()
        except Exception:
            pass

    def reload_http_examples(self) -> None:
        """Tell the HTTP Tester to rebuild its examples dropdown."""
        try:
            self.query_one("#http-pane", HttpTesterPane).reload_examples()
        except Exception:
            pass

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_run_script(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-script"
        self.query_one("#script-pane", ScriptPane).start_run()


if __name__ == "__main__":
    SysMonitorApp().run()
