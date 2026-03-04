#!/usr/bin/env bash
# =============================================================================
# SysMonitor Check Script
# Results are written to RESULTS_FILE (injected by the app via env var).
# Format: "Check Name: PASS" or "Check Name: FAIL"
# Prefix lines with [INFO] for dimmed info, [CHECK] for highlighted status.
# =============================================================================

RESULTS_FILE="${RESULTS_FILE:-$HOME/.sysmonitor/check_results.txt}"
> "$RESULTS_FILE"   # clear previous results

pass() { echo "[CHECK] $1: PASS";  echo "$1: PASS"  >> "$RESULTS_FILE"; }
fail() { echo "[CHECK] $1: FAIL";  echo "$1: FAIL"  >> "$RESULTS_FILE"; }
info() { echo "[INFO]  $1"; }

info "=================================================="
info "System Checks  —  $(date '+%Y-%m-%d %H:%M:%S')"
info "=================================================="

# ---------------------------------------------------------------------------
# 1. Disk usage (root partition < 90%)
# ---------------------------------------------------------------------------
info "Checking disk usage..."
DISK_PCT=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
if [ "${DISK_PCT:-0}" -lt 90 ]; then
    pass "Disk Usage (${DISK_PCT}%)"
else
    fail "Disk Usage (${DISK_PCT}% — exceeds 90%)"
fi

# ---------------------------------------------------------------------------
# 2. Free memory (> 10%)
# ---------------------------------------------------------------------------
info "Checking memory..."
MEM_FREE_PCT=$(free | awk '/^Mem:/ {printf "%.0f", $7/$2*100}')
if [ "${MEM_FREE_PCT:-0}" -gt 10 ]; then
    pass "Memory Available (${MEM_FREE_PCT}% free)"
else
    fail "Memory Available (${MEM_FREE_PCT}% free — LOW)"
fi

# ---------------------------------------------------------------------------
# 3. CPU load (1-min load avg < number of cores)
# ---------------------------------------------------------------------------
info "Checking CPU load..."
LOAD1=$(awk '{print $1}' /proc/loadavg)
CORES=$(nproc 2>/dev/null || echo 1)
# Compare using awk (handles floats)
OVERLOADED=$(awk -v l="$LOAD1" -v c="$CORES" 'BEGIN{print (l > c) ? 1 : 0}')
if [ "$OVERLOADED" -eq 0 ]; then
    pass "CPU Load (${LOAD1} / ${CORES} cores)"
else
    fail "CPU Load (${LOAD1} / ${CORES} cores — OVERLOADED)"
fi

# ---------------------------------------------------------------------------
# 4. Network connectivity (ping 8.8.8.8)
# ---------------------------------------------------------------------------
info "Checking network connectivity..."
if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
    pass "Network Connectivity"
else
    fail "Network Connectivity"
fi

# ---------------------------------------------------------------------------
# 5. DNS resolution
# ---------------------------------------------------------------------------
info "Checking DNS..."
if getent hosts google.com &>/dev/null || host google.com &>/dev/null 2>&1; then
    pass "DNS Resolution"
else
    fail "DNS Resolution"
fi

# ---------------------------------------------------------------------------
# 6. /tmp writable
# ---------------------------------------------------------------------------
info "Checking /tmp writable..."
TMP_TEST=$(mktemp /tmp/.sysmonitor_XXXXXX 2>/dev/null)
if [ -n "$TMP_TEST" ]; then
    rm -f "$TMP_TEST"
    pass "Temp Directory (/tmp) Writable"
else
    fail "Temp Directory (/tmp) Writable"
fi

# ---------------------------------------------------------------------------
# 7. Swap (if any swap exists, check it isn't > 80% used)
# ---------------------------------------------------------------------------
info "Checking swap..."
SWAP_TOTAL=$(free | awk '/^Swap:/ {print $2}')
if [ "${SWAP_TOTAL:-0}" -eq 0 ]; then
    info "  No swap configured — skipping."
    pass "Swap (none configured)"
else
    SWAP_PCT=$(free | awk '/^Swap:/ {printf "%.0f", $3/$2*100}')
    if [ "${SWAP_PCT:-0}" -lt 80 ]; then
        pass "Swap Usage (${SWAP_PCT}%)"
    else
        fail "Swap Usage (${SWAP_PCT}% — HIGH)"
    fi
fi

# ---------------------------------------------------------------------------
# 8. Clock sync (systemd-timesyncd / chrony)
# ---------------------------------------------------------------------------
info "Checking time synchronisation..."
SYNC_OK=0
if timedatectl status 2>/dev/null | grep -q "synchronized: yes"; then
    SYNC_OK=1
elif chronyc tracking &>/dev/null 2>&1; then
    SYNC_OK=1
fi
if [ "$SYNC_OK" -eq 1 ]; then
    pass "Time Synchronisation"
else
    fail "Time Synchronisation"
fi

# ---------------------------------------------------------------------------
# 9. SSH service
# ---------------------------------------------------------------------------
info "Checking SSH service..."
if systemctl is-active --quiet sshd 2>/dev/null || \
   systemctl is-active --quiet ssh  2>/dev/null; then
    pass "SSH Service"
else
    info "  SSH service not active (may not be required)."
    pass "SSH Service (not required)"
fi

# ---------------------------------------------------------------------------
# 10. Open file descriptors (< 80% of system limit)
# ---------------------------------------------------------------------------
info "Checking open file descriptors..."
FD_USED=$(cat /proc/sys/fs/file-nr 2>/dev/null | awk '{print $1}')
FD_MAX=$(cat /proc/sys/fs/file-max 2>/dev/null)
if [ -n "$FD_USED" ] && [ -n "$FD_MAX" ] && [ "$FD_MAX" -gt 0 ]; then
    FD_PCT=$(awk -v u="$FD_USED" -v m="$FD_MAX" 'BEGIN{printf "%.0f", u/m*100}')
    if [ "${FD_PCT:-0}" -lt 80 ]; then
        pass "Open File Descriptors (${FD_PCT}%)"
    else
        fail "Open File Descriptors (${FD_PCT}% — HIGH)"
    fi
else
    info "  Cannot determine file descriptor usage."
    pass "Open File Descriptors (unknown)"
fi

info "=================================================="
info "All checks complete."
info "=================================================="
