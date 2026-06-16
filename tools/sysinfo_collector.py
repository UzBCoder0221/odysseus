#!/usr/bin/env python3
"""sysinfo_collector.py — collect system specs and push to Odysseus companion.

Interactive use: shows a banner, lets you configure or confirm the server
address, runs a health check, collects data with live progress, displays
a full summary, asks for consent, then sends the data.

Usage:
    python sysinfo_collector.py
"""

from sysinfo_helpers import (
    TARGET_URL,
    check_server_health,
    collect_sysinfo,
    format_data_summary,
    post_data,
    press_any_key,
)

BANNER = (
    "\n+" + "=" * 46 + "+\n"
    "|" + "Odysseus System Info Collector".center(46) + "|\n"
    "+" + "=" * 46 + "+"
)


def safe_input(prompt: str) -> str:
    """input() that treats EOF / Ctrl-C as an empty answer instead of
    raising — so unattended/odd terminals degrade to 'quit' gracefully
    rather than crashing with a stack trace."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def resolve_url() -> str:
    """Ask the user for a server URL. Default: TARGET_URL."""
    print(f"  Target server: {TARGET_URL}")
    print("  Press Enter to use this, or type a different address:")
    print("    Examples:")
    print("      http://127.0.0.1:7000")
    print("      http://DESKTOP-ABC123:7000")
    print("      http://192.168.1.50:7000")

    for attempt in range(3):
        raw = safe_input("\n  Server address: ")
        if not raw:
            return TARGET_URL
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw.rstrip("/")
        left = 2 - attempt
        print(f"  Invalid format: must start with http:// or https://  "
              f"({left} attempt{'s' if left != 1 else ''} left)")

    print("  Too many invalid attempts. Using default.")
    return TARGET_URL


def health_check_with_retries(url: str):
    """Returns a working URL, or None if the user gives up."""
    ok, reason = check_server_health(url)
    if ok:
        return url

    print(f"  [FAIL] Cannot reach Odysseus at {url}")
    print(f"    Reason: {reason}")
    print()
    print("    Common fixes:")
    print("    - Make sure Odysseus is running before launching this tool")
    print("    - Try these addresses:")
    print("        http://localhost:7000")
    print("        http://127.0.0.1:7000")
    print("        http://YOUR-PC-HOSTNAME:7000")
    print("    - Check that port 7000 is not blocked by firewall")
    print()

    for attempt in range(3):
        raw = safe_input("  Enter a different server address, or press Enter to quit: ")
        if not raw:
            print("  Giving up.")
            return None
        if not (raw.startswith("http://") or raw.startswith("https://")):
            print("  Invalid URL format. Must start with http:// or https://")
            continue
        candidate = raw.rstrip("/")
        ok, reason = check_server_health(candidate)
        if ok:
            print(f"  [OK] Found server at {candidate}\n")
            return candidate
        print(f"  [FAIL] {reason}")

    print("  Giving up.")
    return None


def run() -> None:
    print(BANNER)

    url = resolve_url()
    print(f"\n  Checking {url} ...")

    url = health_check_with_retries(url)
    if not url:
        return
    print(f"  [OK] Using server: {url}\n")

    # Collect — announce each section as it starts
    print("  Collecting system information...")

    def on_progress(label):
        print(f"    -> {label}...")

    info = collect_sysinfo(progress_callback=on_progress)

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
        print("\n  [OK] System info saved to your Odysseus companion.")
        print("  Open Odysseus -> companion panel -> Profile -> System Info.")
    else:
        print(f"\n  [FAIL] {msg}")
        print("  Your system data was NOT saved. You can try running this tool again.")


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
    except Exception as e:  # noqa: BLE001 — never let the window vanish silently
        print(f"\n  [FAIL] Unexpected error: {e}")
    finally:
        press_any_key()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()