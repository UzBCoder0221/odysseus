#!/usr/bin/env python3
"""sysinfo_collector_silent.py — background/automated variant.

Tries to find Odysseus automatically (localhost, 127.0.0.1, own hostname)
and sync silently with no prompts. Falls back to bringing the window to
the foreground and asking the user for a server address if automatic
discovery or sync fails for any reason.

Usage:
    python sysinfo_collector_silent.py
"""

import socket

from sysinfo_helpers import (
    check_server_health,
    collect_sysinfo,
    format_data_summary,
    post_data,
    press_any_key,
)
from sysinfo_collector import BANNER, health_check_with_retries, safe_input

try:
    import ctypes
    _HWND = ctypes.windll.kernel32.GetConsoleWindow() if hasattr(ctypes, "windll") else None
except Exception:  # noqa: BLE001
    _HWND = None


def _minimize_window():
    if not _HWND:
        return
    try:
        ctypes.windll.user32.ShowWindow(_HWND, 6)  # SW_MINIMIZE
    except Exception:  # noqa: BLE001
        pass


def _restore_window():
    if not _HWND:
        return
    try:
        ctypes.windll.user32.ShowWindow(_HWND, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(_HWND)
    except Exception:  # noqa: BLE001
        pass


def _candidate_urls():
    urls = ["http://127.0.0.1:7000", "http://localhost:7000"]
    try:
        host = socket.gethostname()
        if host:
            urls.append(f"http://{host}:7000")
    except Exception:  # noqa: BLE001
        pass
    return urls


def _try_auto_discover():
    for url in _candidate_urls():
        ok, _ = check_server_health(url, timeout=3)
        if ok:
            return url
    return None


def fallback_interactive(reason: str):
    """Bring the window to focus and run the normal interactive flow."""
    _restore_window()
    print(BANNER)
    print(f"  [!] Automatic server detection failed.")
    print(f"      Reason: {reason}")
    print()
    print("  Enter your Odysseus server address, or press Enter to quit.")
    print("    Examples:")
    print("      http://localhost:7000")
    print("      http://127.0.0.1:7000")
    print("      http://DESKTOP-ABC123:7000")
    print("      http://192.168.1.50:7000")

    raw = safe_input("\n  Server address: ")
    if not raw:
        print("  Cancelled.")
        return
    if not (raw.startswith("http://") or raw.startswith("https://")):
        print("  Invalid URL format. Must start with http:// or https://")
        return

    url = health_check_with_retries(raw.rstrip("/"))
    if not url:
        return

    _run_sync(url)


def _run_sync(url: str):
    info = collect_sysinfo()

    print()
    print(format_data_summary(info))
    consent = safe_input(f"  Send this data to Odysseus at {url}? [y/N]: ")
    if consent.lower() != "y":
        print("  Cancelled. Nothing was sent.")
        return
    owner = safe_input("  Your Odysseus username: ")
    if not owner:
        print("  No username given. Nothing was sent.")
        return

    success, msg = post_data(url, owner, info)
    if success:
        print("  [OK] System info saved to your Odysseus companion.")
    else:
        print(f"  [FAIL] {msg}")
        print("  Your system data was NOT saved.")


def run():
    _minimize_window()

    url = _try_auto_discover()
    if not url:
        fallback_interactive("Could not reach Odysseus on any known address "
                              "(localhost, 127.0.0.1, or this PC's hostname)")
        return

    info = collect_sysinfo()
    success, msg = post_data(url, "auto", info)

    if success:
        _restore_window()
        print(BANNER)
        print(f"  [OK] Found Odysseus automatically at {url}")
        print("  [OK] System info saved to your Odysseus companion.")
        return

    fallback_interactive(msg)


def main():
    try:
        run()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
    except Exception as e:  # noqa: BLE001
        _restore_window()
        print(f"\n  [FAIL] Unexpected error: {e}")
    finally:
        press_any_key()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()