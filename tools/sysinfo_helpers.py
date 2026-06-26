#!/usr/bin/env python3
"""sysinfo_helpers.py — shared collection/formatting/networking logic
used by both sysinfo_collector.py (interactive) and
sysinfo_collector_silent.py (silent/auto).

Every individual collector function is wrapped with a hard timeout via
a daemon thread, so a slow/blocked call (py-cpuinfo, GPUtil, screeninfo,
wmi all occasionally hang inside VMs/containers) can never freeze the
whole script. The thread is fired with daemon=True and we just stop
waiting on it after the timeout — we don't try to kill it, Python can't
safely do that, but a daemon thread won't keep the process alive either.
"""

import hashlib
import platform
import socket
import threading
import time
from datetime import datetime, timezone

import requests

# ─────────────────────────────────────────────────────────
# CHANGE THIS to your Odysseus server address:
TARGET_URL = "http://localhost:7000"
# ─────────────────────────────────────────────────────────

SYSINFO_ENDPOINT = "/api/companion/sysinfo"
HEALTH_ENDPOINTS = ("/api/health", "/")
SYSINFO_TOKEN = "sysinfo"
COLLECTOR_VERSION = "1.1.0"

PER_SECTION_TIMEOUT = 5  # seconds — generous, but bounded


# ─────────────────────────────────────────────────────────
# Timeout wrapper
# ─────────────────────────────────────────────────────────

def _run_with_timeout(fn, timeout=PER_SECTION_TIMEOUT, default=None):
    """Run fn() in a daemon thread, wait up to `timeout` seconds for a
    result. If it doesn't finish in time, abandon it and return `default`
    instead of blocking forever. This is intentionally NOT
    concurrent.futures.ThreadPoolExecutor — that pool's shutdown(wait=True)
    on context-manager exit blocks until the worker actually finishes,
    which defeats the whole point of a timeout. A plain daemon Thread with
    .join(timeout) returns control to us regardless of whether the
    worker is still stuck.
    """
    result = {"value": default, "error": None}

    def _target():
        try:
            result["value"] = fn()
        except Exception as e:  # noqa: BLE001 - we want to swallow everything here
            result["error"] = str(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        # Timed out — thread is abandoned (daemon, won't block exit)
        return {"status": "timed_out"}
    if result["error"]:
        return {"status": "error", "detail": result["error"]}
    return result["value"]


# ─────────────────────────────────────────────────────────
# Individual collectors — each one is self-contained and safe to fail
# ─────────────────────────────────────────────────────────

def _collect_os():
    try:
        return {
            "name": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)}


def _detect_cpu_brand():
    """Get a human-readable CPU brand string WITHOUT py-cpuinfo.

    py-cpuinfo internally isolates risky CPUID reads by spawning
    Popen([sys.executable, "-c", ...]) — safe in a normal Python
    install (sys.executable is python.exe), but catastrophic inside a
    PyInstaller --onefile exe: sys.executable IS the frozen exe
    itself, so that call recursively RE-LAUNCHES THE ENTIRE PROGRAM in
    the background. That re-prints the banner, re-asks for server
    address, and leaves a zombie process competing for stdin/stdout —
    which is exactly what caused the duplicated banners and the
    swallowed "y" keystroke during testing.

    These methods are either subprocess-free, or call a real system
    binary (never sys.executable), so they're safe to freeze.
    """
    system = platform.system()

    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if name:
                return name.strip()
        except Exception:  # noqa: BLE001
            pass

    if system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:  # noqa: BLE001
            pass

    if system == "Darwin":
        try:
            import subprocess
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:  # noqa: BLE001
            pass

    return platform.processor() or "unknown"


def _collect_cpu():
    out = {"status": "ok"}
    try:
        import psutil
        out["physical_cores"] = psutil.cpu_count(logical=False)
        out["logical_cores"] = psutil.cpu_count(logical=True)
        freq = psutil.cpu_freq()
        if freq:
            out["current_mhz"] = round(freq.current, 1)
            out["max_mhz"] = round(freq.max, 1) if freq.max else None
        out["usage_percent"] = psutil.cpu_percent(interval=0.5)
    except Exception as e:  # noqa: BLE001
        out["psutil_error"] = str(e)

    out["brand"] = _detect_cpu_brand()
    return out


def _collect_ram():
    try:
        import psutil
        vm = psutil.virtual_memory()
        gb = 1024 ** 3
        return {
            "total_gb": round(vm.total / gb, 2),
            "available_gb": round(vm.available / gb, 2),
            "used_gb": round(vm.used / gb, 2),
            "percent_used": vm.percent,
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)}


def _collect_disks():
    disks = []
    try:
        import psutil
        gb = 1024 ** 3
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mount": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / gb, 2),
                    "used_gb": round(usage.used / gb, 2),
                    "free_gb": round(usage.free / gb, 2),
                    "percent_used": usage.percent,
                })
            except (PermissionError, OSError):
                continue
    except Exception:  # noqa: BLE001
        pass
    return disks


def _collect_gpus():
    gpus = []

    # Method 1 — GPUtil (NVIDIA only)
    try:
        import GPUtil
        for g in GPUtil.getGPUs():
            gpus.append({
                "name": g.name,
                "vram_total_mb": round(g.memoryTotal),
                "vram_free_mb": round(g.memoryFree),
                "driver": g.driver,
                "usage_percent": round(g.load * 100, 1),
                "source": "GPUtil",
            })
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass

    if gpus:
        return gpus

    # Method 2 — Windows WMI
    if platform.system() == "Windows":
        try:
            import wmi
            w = wmi.WMI()
            for gpu in w.Win32_VideoController():
                vram_mb = None
                if gpu.AdapterRAM:
                    vram_mb = round(gpu.AdapterRAM / (1024 ** 2))
                gpus.append({
                    "name": gpu.Name,
                    "vram_total_mb": vram_mb,
                    "driver": gpu.DriverVersion,
                    "source": "wmi",
                })
        except ImportError:
            pass
        except Exception:  # noqa: BLE001
            pass

    if gpus:
        return gpus

    # Method 3 — wmic subprocess fallback (older Windows, no wmi package needed)
    if platform.system() == "Windows":
        try:
            import subprocess
            out = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get",
                 "name,AdapterRAM,DriverVersion", "/format:csv"],
                capture_output=True, text=True, timeout=4,
            )
            lines = [l for l in out.stdout.splitlines() if l.strip()]
            if len(lines) > 1:
                header = lines[0].split(",")
                for line in lines[1:]:
                    vals = line.split(",")
                    if len(vals) != len(header):
                        continue
                    row = dict(zip(header, vals))
                    ram = row.get("AdapterRAM")
                    vram_mb = round(int(ram) / (1024 ** 2)) if ram and ram.isdigit() else None
                    gpus.append({
                        "name": row.get("Name", "unknown"),
                        "vram_total_mb": vram_mb,
                        "driver": row.get("DriverVersion"),
                        "source": "wmic",
                    })
        except Exception:  # noqa: BLE001
            pass

    if not gpus:
        return [{"status": "detection_failed"}]
    return gpus


def _collect_npu():
    if platform.system() != "Windows":
        return None
    try:
        import wmi
        w = wmi.WMI()
        keywords = ("neural", "npu", "ai processor", "vpu", "xdna")
        found = []
        for dev in w.Win32_PnPEntity():
            name = (dev.Name or "").lower()
            if any(k in name for k in keywords):
                found.append({"name": dev.Name})
        return found or None
    except Exception:  # noqa: BLE001
        return None


def _collect_displays():
    displays = []
    try:
        import screeninfo
        for i, m in enumerate(screeninfo.get_monitors()):
            displays.append({
                "index": i,
                "resolution": f"{m.width}x{m.height}",
                "primary": getattr(m, "is_primary", None),
            })
    except ImportError:
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            displays.append({
                "index": 0,
                "resolution": f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}",
                "primary": True,
            })
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return displays


def _collect_battery():
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return None
        return {
            "percent": round(b.percent, 1),
            "plugged_in": b.power_plugged,
            "minutes_remaining": (
                None if b.secsleft in (psutil.POWER_TIME_UNLIMITED, None) or b.secsleft < 0
                else round(b.secsleft / 60)
            ),
        }
    except Exception:  # noqa: BLE001
        return None


def _collect_network():
    try:
        import psutil
        return sorted(psutil.net_if_addrs().keys())
    except Exception:  # noqa: BLE001
        return []


def _collect_uptime():
    try:
        import psutil
        seconds = time.time() - psutil.boot_time()
        return round(seconds / 3600, 1)
    except Exception:  # noqa: BLE001
        return None


def _hashed_hostname():
    try:
        raw = socket.gethostname()
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        return "unknown"


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def collect_sysinfo(progress_callback=None):
    """Collect everything. progress_callback(label) is called right
    before each section starts, if provided (used for live status
    printing in the interactive version)."""

    def _step(label, fn, timeout=PER_SECTION_TIMEOUT, default=None):
        if progress_callback:
            progress_callback(label)
        return _run_with_timeout(fn, timeout=timeout, default=default)

    info = {
        "collector_version": COLLECTOR_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "hostname_hash": _hashed_hostname(),
        "os": _step("OS", _collect_os, default={"status": "error"}),
        "cpu": _step("CPU", _collect_cpu, default={"status": "error"}),
        "ram": _step("RAM", _collect_ram, default={"status": "error"}),
        "disks": _step("Storage", _collect_disks, default=[]),
        "gpus": _step("GPU", _collect_gpus, default=[{"status": "detection_failed"}]),
        "npu": _step("NPU", _collect_npu, default=None),
        "displays": _step("Display", _collect_displays, default=[]),
        "battery": _step("Battery", _collect_battery, default=None),
        "network_interfaces": _step("Network", _collect_network, default=[]),
        "uptime_hours": _step("Uptime", _collect_uptime, default=None),
    }
    return info


def check_server_health(base_url, timeout=5):
    """Returns (ok: bool, reason: str)."""
    last_reason = "Unknown error"
    for path in HEALTH_ENDPOINTS:
        url = base_url.rstrip("/") + path
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code < 500:
                return True, "OK"
            last_reason = f"Server returned HTTP {r.status_code}"
        except requests.exceptions.ConnectionError:
            last_reason = "Connection refused — server is not running or wrong address"
        except requests.exceptions.Timeout:
            last_reason = f"No response within {timeout}s — server may be overloaded or wrong address"
        except requests.exceptions.InvalidURL:
            return False, "Invalid URL format"
        except Exception as e:  # noqa: BLE001
            last_reason = str(e)
    return False, last_reason


def format_data_summary(info):
    lines = []
    line = lines.append
    w = 48

    line("+" + "=" * w + "+")
    line("|" + "DATA THAT WILL BE SENT".center(w) + "|")
    line("+" + "=" * w + "+")
    line("")

    os_info = info.get("os", {})
    line("SYSTEM")
    line(f"  OS            {os_info.get('name', '?')} {os_info.get('release', '')}")
    line(f"  Architecture  {os_info.get('architecture', '?')}")
    line("")

    cpu = info.get("cpu", {})
    line("CPU")
    line(f"  Model         {cpu.get('brand', 'unknown')}")
    line(f"  Cores         {cpu.get('physical_cores', '?')} physical / {cpu.get('logical_cores', '?')} logical")
    if cpu.get("current_mhz"):
        line(f"  Speed         {cpu['current_mhz']} MHz current"
             + (f" / {cpu['max_mhz']} MHz max" if cpu.get("max_mhz") else ""))
    if cpu.get("l2_cache") or cpu.get("l3_cache"):
        l2 = cpu.get("l2_cache")
        l3 = cpu.get("l3_cache")
        line(f"  Cache         L2: {l2 or 'n/a'}  L3: {l3 or 'n/a'}")
    line(f"  Usage now     {cpu.get('usage_percent', '?')}%")
    line("")

    ram = info.get("ram", {})
    line("RAM")
    line(f"  Total         {ram.get('total_gb', '?')} GB")
    line(f"  Available     {ram.get('available_gb', '?')} GB")
    line(f"  Used          {ram.get('used_gb', '?')} GB ({ram.get('percent_used', '?')}%)")
    line("")

    disks = info.get("disks", [])
    line("STORAGE")
    if disks:
        for d in disks:
            line(f"  {d['mount']:<12} {d['total_gb']} GB total | {d['free_gb']} GB free | {d['fstype']}")
    else:
        line("  (none detected)")
    line("")

    gpus = info.get("gpus", [])
    line("GPU(s)")
    if gpus and gpus[0].get("status") != "detection_failed":
        for i, g in enumerate(gpus):
            vram = g.get("vram_total_mb")
            vram_str = f"{vram} MB VRAM" if vram else "VRAM unknown"
            line(f"  [{i}] {g.get('name', 'unknown')}  |  {vram_str}")
    else:
        line("  (detection failed / not available)")
    line("")

    npu = info.get("npu")
    line("NPU")
    if npu:
        for n in npu:
            line(f"  {n.get('name', 'unknown')} (detected)")
    else:
        line("  Not detected")
    line("")

    displays = info.get("displays", [])
    line("DISPLAY(s)")
    if displays:
        for d in displays:
            tag = " (primary)" if d.get("primary") else ""
            line(f"  [{d['index']}] {d['resolution']}{tag}")
    else:
        line("  (none detected)")
    line("")

    battery = info.get("battery")
    line("BATTERY")
    if battery:
        plug = "Plugged in" if battery.get("plugged_in") else "Not plugged in"
        line(f"  {battery.get('percent', '?')}%  |  {plug}")
    else:
        line("  Not present")
    line("")

    net = info.get("network_interfaces", [])
    line("NETWORK INTERFACES")
    line(f"  {', '.join(net) if net else '(none found)'}  (names only, no IPs or MACs)")
    line("")

    line("-" * (w + 2))
    line("No passwords, IPs, MAC addresses, or personal files are included.")
    line("Hostname is stored as an anonymized hash.")
    line("-" * (w + 2))

    return "\n".join(lines)


def post_data(base_url, owner, info, timeout=(5, 15)):
    """Returns (success: bool, message: str).

    Payload shape is {"owner": ..., "system_info": {...}} — the backend
    validates for these two top-level keys specifically (confirmed by
    its "Missing owner or system_info" error), so the actual collected
    data must be nested under "system_info", not sent flat.
    """
    url = base_url.rstrip("/") + SYSINFO_ENDPOINT
    payload = {
        "owner": owner,
        "system_info": info,
    }
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"X-Odysseus-Token": SYSINFO_TOKEN},
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        return False, "Lost connection to server during upload"
    except requests.exceptions.Timeout:
        return False, "Server took too long to respond (timed out)"
    except Exception as e:  # noqa: BLE001
        return False, f"Unexpected error: {e}"

    if r.status_code == 200:
        return True, "OK"
    if r.status_code in (401, 403):
        return False, "Auth rejected — wrong token or server config"
    if r.status_code >= 500:
        return False, "Server error — check Odysseus logs"
    body_preview = (r.text or "")[:200]
    return False, f"Unexpected HTTP {r.status_code}: {body_preview}"


def press_any_key():
    print("\n  Press Enter to exit...")
    try:
        import msvcrt
        msvcrt.getch()
    except ImportError:
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass