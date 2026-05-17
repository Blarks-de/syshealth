#!/usr/bin/env python3
"""
syshealth.py — System-Gesundheitscheck
- VM vs. Bare-Metal Erkennung
- RAM-Status
- SMART-Status (HDD/SSD)
- NVMe-Gesundheit
- CPU & Temperaturen
- Uptime & Load

Abhängigkeiten:
  pip install psutil
  Linux: smartmontools installiert (smartctl)
  Windows: smartmontools für Windows oder CrystalDiskInfo CLI
"""

import subprocess
import platform
import sys
import os
import json
import re
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:
    print("[!] psutil fehlt. Bitte installieren: pip install psutil")
    sys.exit(1)

SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == "windows"
IS_LINUX   = SYSTEM == "linux"

SEP = "─" * 60


def run(cmd: list, timeout: int = 10) -> tuple[int, str, str]:
    """Führt einen Befehl aus, gibt (returncode, stdout, stderr) zurück.
    Auf Windows wird die OEM-Codepage (GetOEMCP) verwendet — sonst UTF-8."""
    if IS_WINDOWS:
        try:
            import ctypes
            ansi_cp = ctypes.windll.kernel32.GetACP()
            fallback_enc = f"cp{ansi_cp}"
        except Exception:
            fallback_enc = "cp1252"
    else:
        fallback_enc = "utf-8"

    def _decode(b: bytes) -> str:
        if not b:
            return ""
        if IS_WINDOWS:
            # Moderne Windows-Tools (manage-bde, PowerShell 7) geben UTF-8 aus;
            # ältere Tools (wmic, net) nutzen ANSI (cp1252) — UTF-8 zuerst probieren
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode(fallback_enc, errors="replace")
        return b.decode("utf-8", errors="replace")

    try:
        r = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout
        )
        stdout = _decode(r.stdout)
        stderr = _decode(r.stderr)
        return r.returncode, stdout, stderr
    except FileNotFoundError:
        return -1, "", f"Befehl nicht gefunden: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except PermissionError:
        return -1, "", "Keine Berechtigung (root/Admin nötig?)"


def header(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


# ─────────────────────────────────────────────
# 1. VM-ERKENNUNG
# ─────────────────────────────────────────────
def detect_vm() -> tuple[bool, str]:
    """Gibt (is_vm, grund) zurück."""
    hints = []

    if IS_LINUX:
        # systemd-detect-virt (zuverlässigste Methode)
        rc, out, _ = run(["systemd-detect-virt"])
        if rc == 0 and out.strip() not in ("none", ""):
            return True, f"systemd-detect-virt: {out.strip()}"

        # DMI-Strings prüfen
        dmi_paths = [
            "/sys/class/dmi/id/product_name",
            "/sys/class/dmi/id/sys_vendor",
            "/sys/class/dmi/id/board_vendor",
        ]
        vm_keywords = [
            "vmware", "virtualbox", "kvm", "qemu", "xen", "hyper-v",
            "microsoft corporation", "bochs", "parallels", "docker",
            "proxmox", "virtual machine", "bhyve"
        ]
        for path in dmi_paths:
            try:
                with open(path) as f:
                    val = f.read().strip().lower()
                for kw in vm_keywords:
                    if kw in val:
                        hints.append(f"{os.path.basename(path)}: {val}")
            except (OSError, IOError):
                pass

        # /proc/cpuinfo auf Hypervisor-Flag prüfen
        try:
            with open("/proc/cpuinfo") as f:
                if "hypervisor" in f.read():
                    hints.append("cpuinfo: hypervisor-Flag gesetzt")
        except OSError:
            pass

        # Bekannte VM-Kernelmodule
        rc, out, _ = run(["lsmod"])
        if rc == 0:
            vm_mods = ["vboxguest", "vmw_vmci", "virtio", "xen_blkfront"]
            for mod in vm_mods:
                if mod in out:
                    hints.append(f"Kernelmodul: {mod}")

    elif IS_WINDOWS:
        rc, out, _ = run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Model) + ' ' + (Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer)"
        ], timeout=15)
        if rc == 0:
            vm_keywords = ["vmware", "virtualbox", "kvm", "qemu", "hyper-v", "virtual"]
            for kw in vm_keywords:
                if kw in out.lower():
                    hints.append(f"CimInstance: {out.strip()[:80]}")
                    break

    if hints:
        return True, " | ".join(hints)
    return False, "Kein VM-Indikator gefunden"


def check_vm():
    header("🖥️  VM vs. Bare-Metal")
    is_vm, reason = detect_vm()
    if is_vm:
        print(f"  Status : ⚠️  VIRTUELLE MASCHINE")
        print(f"  Grund  : {reason}")
    else:
        print(f"  Status : ✅  Bare-Metal (physische Maschine)")
        print(f"  Info   : {reason}")
    return is_vm


# ─────────────────────────────────────────────
# 2. RAM
# ─────────────────────────────────────────────
def check_ram():
    header("🧠  RAM")
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    def fmt(b): return f"{b / 1024**3:.2f} GB"

    used_pct = mem.percent
    status = "✅" if used_pct < 80 else ("⚠️ " if used_pct < 92 else "❌")

    print(f"  Gesamt     : {fmt(mem.total)}")
    print(f"  Verfügbar  : {fmt(mem.available)}")
    print(f"  Genutzt    : {fmt(mem.used)} ({used_pct:.1f}%)  {status}")
    print(f"  Swap gesamt: {fmt(swap.total)}")
    print(f"  Swap genutzt: {fmt(swap.used)} ({swap.percent:.1f}%)")

    # Auf Linux: Speicherdetails aus /proc/meminfo
    if IS_LINUX:
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])
            dirty_mb = info.get("Dirty", 0) / 1024
            total_mb = mem.total / 1024 / 1024
            threshold_mb = total_mb * 0.02  # 2% des Gesamt-RAM
            if dirty_mb > threshold_mb:
                print(f"  ⚠️  Dirty Pages: {dirty_mb:.0f} MB (>{threshold_mb:.0f} MB = 2% des RAM — viel ungeschriebener Cache)")
        except OSError:
            pass


# ─────────────────────────────────────────────
# 3. SMART (HDD/SSD) + NVMe
# ─────────────────────────────────────────────
def get_block_devices() -> list[str]:
    """Gibt eine Liste von Blockgeräten zurück."""
    devices = []
    if IS_LINUX:
        try:
            entries = os.listdir("/sys/block")
            for e in sorted(entries):
                # Nur echte Platten, keine Loop/RAM/etc.
                if re.match(r"^(sd|hd|nvme|vd|xvd)[a-z0-9]+$", e):
                    # Nur Top-Level-Geräte (keine Partitionen)
                    if not re.search(r"\d+$", e) or "nvme" in e:
                        if "nvme" in e and re.search(r"n\d+$", e):
                            devices.append(f"/dev/{e}")
                        elif "nvme" not in e:
                            devices.append(f"/dev/{e}")
        except OSError:
            pass
    elif IS_WINDOWS:
        rc, out, _ = run(["wmic", "diskdrive", "get", "DeviceID", "/format:list"])
        if rc == 0:
            for line in out.splitlines():
                if "DeviceID" in line:
                    dev = line.split("=", 1)[-1].strip()
                    if dev:
                        devices.append(dev)
    return devices


def smartctl_available() -> bool:
    rc, _, _ = run(["smartctl", "--version"])
    return rc == 0


def check_smart_windows_native():
    """Windows-nativer Speicher-Health-Check via PowerShell (ohne smartctl)."""
    print("\n  Windows-nativer Speicher-Health-Check (PowerShell):")
    rc, out, _ = run([
        "powershell", "-NoProfile", "-Command",
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,"
        "@{N='GB';E={[math]::Round($_.Size/1GB,0)}} | Format-Table -AutoSize | Out-String -Width 120"
    ], timeout=15)
    if rc != 0 or not out.strip():
        print("    ⚠️  Get-PhysicalDisk nicht verfügbar")
        return
    for line in out.strip().splitlines():
        ls = line.strip()
        if not ls or ls.startswith("-") or "FriendlyName" in ls:
            continue
        if any(w in ls for w in ("Healthy", "Fehlerfrei")):
            print(f"    ✅  {ls}")
        elif any(w in ls for w in ("Warning", "Warnung")):
            print(f"    ⚠️  {ls}")
        elif any(w in ls for w in ("Unhealthy", "Fehlerhaft")):
            print(f"    ❌  {ls}")
        else:
            print(f"    ·   {ls}")

    # Reliability Counter (Temperaturen, Fehler, Wear)
    rc2, out2, _ = run([
        "powershell", "-NoProfile", "-Command",
        "Get-StorageReliabilityCounter -PhysicalDisk (Get-PhysicalDisk) | "
        "Select-Object DeviceId,Temperature,Wear,ReadErrorsTotal,WriteErrorsTotal | "
        "Format-Table -AutoSize | Out-String -Width 120"
    ], timeout=15)
    if rc2 == 0 and out2.strip():
        lines = [l for l in out2.strip().splitlines()
                 if l.strip() and not l.strip().startswith("-") and "DeviceId" not in l]
        if lines:
            print(f"\n  Reliability Counter (Wear = Abnutzung in %, Temperatur in °C):")
            for line in lines:
                ls = line.strip()
                # Wear-Warnung: letztes numerisches Feld prüfen
                wear_warn = ""
                m = re.search(r"\s(\d+)\s*$", ls)
                if m:
                    try:
                        if int(m.group(1)) > 80:
                            wear_warn = "  ⚠️ Wear hoch!"
                    except ValueError:
                        pass
                print(f"    {ls}{wear_warn}")


def parse_smart_overall(output: str) -> tuple[str, str]:
    """Extrahiert SMART-Gesamturteil."""
    for line in output.splitlines():
        if "SMART overall-health" in line or "SMART Health Status" in line:
            if "PASSED" in line or "OK" in line:
                return "✅ PASSED", line.strip()
            else:
                return "❌ FAILED", line.strip()
    return "❓ Unbekannt", ""


SMART_THRESHOLDS = {
    "5":   {"name": "Reallocated Sectors",       "warn": 1,   "crit": 50},
    "10":  {"name": "Spin Retry Count",           "warn": 1,   "crit": 5},
    "187": {"name": "Reported Uncorrectable",     "warn": 1,   "crit": 10},
    "188": {"name": "Command Timeout",            "warn": 5,   "crit": 50},
    "196": {"name": "Reallocation Events",        "warn": 1,   "crit": 50},
    "197": {"name": "Current Pending Sectors",    "warn": 1,   "crit": 10},
    "198": {"name": "Offline Uncorrectable",      "warn": 1,   "crit": 5},
    "199": {"name": "UDMA CRC Errors",            "warn": 5,   "crit": 50},
}


def parse_smart_attrs(output: str) -> list[dict]:
    """Parst kritische SMART-Attribute mit Schwellwerten."""
    results = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[0].isdigit():
            attr_id = parts[0]
            if attr_id in SMART_THRESHOLDS:
                try:
                    raw = int(parts[-1])
                    t = SMART_THRESHOLDS[attr_id]
                    level = "crit" if raw >= t["crit"] else ("warn" if raw >= t["warn"] else "ok")
                    results.append({"id": attr_id, "name": t["name"], "raw": raw, "level": level})
                except ValueError:
                    pass
    return results


def check_nvme_details(device: str, output: str, dtype: str = None):
    """Zeigt NVMe-spezifische Felder."""
    fields = {
        "temperature":              "Temperatur",
        "available_spare":          "Spare-Kapazität",
        "percentage_used":          "Abnutzung",
        "data_units_written":       "Geschrieben (Units)",
        "media_errors":             "Medienfehler",
        "num_err_log_entries":      "Fehlerlog-Einträge",
        "power_on_hours":           "Betriebsstunden",
    }
    try:
        cmd_nvme = ["smartctl", "-a", "-j"]
        if dtype:
            cmd_nvme.extend(["-d", dtype])
        cmd_nvme.append(device)
        rc, out, _ = run(cmd_nvme, timeout=15)
        if rc in (0, 4) and out:  # rc=4 = einige Fehler aber Daten vorhanden
            data = json.loads(out)
            nvme = data.get("nvme_smart_health_information_log", {})
            if nvme:
                print(f"  {'─'*40}")
                print(f"  NVMe Details:")
                for key, label in fields.items():
                    val = nvme.get(key)
                    if val is not None:
                        if key == "temperature":
                            warn = "⚠️ " if val > 70 else ""
                            print(f"    {label:<22}: {val}°C {warn}")
                        elif key == "percentage_used":
                            warn = "⚠️ " if val > 80 else ("❌" if val > 95 else "")
                            print(f"    {'Schreibzyklen-Verbrauch':<22}: {val}%  {warn}  ← NVMe-Endurance")
                        elif key == "available_spare":
                            warn = "⚠️ " if val < 20 else ""
                            print(f"    {label:<22}: {val}%  {warn}")
                        elif key == "media_errors":
                            warn = "❌" if val > 0 else "✅"
                            print(f"    {label:<22}: {val}  {warn}")
                        elif key == "data_units_written":
                            tb_written = val * 512000 / 1e12
                            print(f"    {'Geschrieben (gesamt)':<22}: {tb_written:.2f} TB  ({val} Units)")
                        elif key == "power_on_hours":
                            days  = val // 24
                            years = days // 365
                            rem_d = days % 365
                            months = rem_d // 30
                            rem_d2 = rem_d % 30
                            parts_t = []
                            if years:  parts_t.append(f"{years} J")
                            if months: parts_t.append(f"{months} Mo")
                            parts_t.append(f"{rem_d2} Tage")
                            readable = " ".join(parts_t)
                            since = datetime.now() - timedelta(hours=val)
                            print(f"    {'Betriebsstunden':<22}: {val} h  ({readable})  — in Betrieb seit ca. {since.strftime('%Y-%m')}")
                        else:
                            print(f"    {label:<22}: {val}")
    except (json.JSONDecodeError, KeyError):
        pass


def check_smart():
    header("💾  SMART & NVMe Gesundheit")

    if not smartctl_available():
        print("  ⚠️  smartctl nicht gefunden.")
        print("       Linux: sudo apt install smartmontools")
        print("       Windows: https://www.smartmontools.org/wiki/Download")
        if IS_WINDOWS:
            check_smart_windows_native()
        return

    # Windows: smartctl --scan für korrekte Geräte- und Typerkennung
    if IS_WINDOWS:
        rc_scan, out_scan, _ = run(["smartctl", "--scan"])
        if rc_scan != 0 or not out_scan.strip():
            print("  ⚠️  smartctl --scan fehlgeschlagen — nutze Windows-nativen Check")
            check_smart_windows_native()
            return
        for scan_line in out_scan.strip().splitlines():
            # Format: "/dev/sda -d nvme # /dev/sda, NVMe device"
            scan_parts = scan_line.split()
            if not scan_parts:
                continue
            dev = scan_parts[0]
            dtype = None
            if "-d" in scan_parts:
                idx = scan_parts.index("-d")
                if idx + 1 < len(scan_parts):
                    dtype = scan_parts[idx + 1]

            print(f"\n  Gerät: {dev}")

            # Modell, Serial, Kapazität
            cmd_i = ["smartctl", "-i", "-j"] + (["-d", dtype] if dtype else []) + [dev]
            rc_i, out_i, _ = run(cmd_i, timeout=15)
            if rc_i in (0, 4) and out_i.strip():
                try:
                    info = json.loads(out_i)
                    model      = info.get("model_name", "")
                    serial     = info.get("serial_number", "")
                    capacity   = info.get("user_capacity", {}).get("bytes", 0)
                    rpm        = info.get("rotation_rate", 0)
                    drive_type = "HDD" if rpm and rpm > 0 else "SSD/NVMe"
                    cap_str    = f"{capacity / 1e9:.0f} GB" if capacity else ""
                    if model:
                        print(f"    🏷️   {model}  |  S/N: {serial}  |  {cap_str}  [{drive_type}]")
                except (json.JSONDecodeError, KeyError):
                    pass

            # Health + kritische Attribute
            cmd_h = ["smartctl", "-H", "-A"] + (["-d", dtype] if dtype else []) + [dev]
            rc, out, err = run(cmd_h, timeout=20)
            if rc == -1:
                print(f"    ❌ Fehler: {err}")
                continue
            if "Unable to detect device type" in out + err:
                print("    ⚠️  Gerätetyp nicht erkannt — Als Administrator ausführen?")
                check_smart_windows_native()
                return

            overall, _ = parse_smart_overall(out)
            print(f"    Gesundheit: {overall}")

            attrs = parse_smart_attrs(out)
            if attrs:
                crits = [a for a in attrs if a["level"] == "crit"]
                warns = [a for a in attrs if a["level"] == "warn"]
                if crits:
                    print("    ❌  KRITISCH — Platte möglicherweise am Sterben!")
                    for a in crits:
                        print(f"       [{a['id']:>3}] {a['name']:<28}: {a['raw']:>6}  ← SEHR HOCH")
                if warns:
                    print("    ⚠️   Erhöhte Werte:")
                    for a in warns:
                        print(f"       [{a['id']:>3}] {a['name']:<28}: {a['raw']:>6}")
                if not crits and not warns:
                    print("    ✅  Alle kritischen Attribute unauffällig")

            if dtype == "nvme":
                check_nvme_details(dev, out, dtype)
        return  # Windows-Pfad abgeschlossen

    # Linux-Pfad
    devices = get_block_devices()
    if not devices:
        print("  Keine Blockgeräte gefunden.")
        return

    for dev in devices:
        # Mountpoints für dieses Gerät ermitteln
        rc_lbl, out_lbl, _ = run([
            "lsblk", "-o", "NAME,MOUNTPOINT,SIZE,FSTYPE",
            "-J", dev
        ])
        mounts = []
        is_root_dev = False
        if rc_lbl == 0 and out_lbl.strip():
            try:
                lbl_data = json.loads(out_lbl)
                def collect_mounts(devs):
                    for d in devs:
                        mp = d.get("mountpoint") or ""
                        if mp:
                            mounts.append((d["name"], mp, d.get("size",""), d.get("fstype","")))
                        if d.get("children"):
                            collect_mounts(d["children"])
                collect_mounts(lbl_data.get("blockdevices", []))
            except (json.JSONDecodeError, KeyError):
                pass
        is_root_dev = any(mp == "/" for _, mp, _, _ in mounts)

        # Geräte-Header
        role = "  ← 🖥️  ROOT-PARTITION" if is_root_dev else ""
        print(f"\n  Gerät: {dev}{role}")
        if mounts:
            for name, mp, size, fstype in mounts:
                marker = " ← ROOT" if mp == "/" else ""
                print(f"    📂  /dev/{name}  →  {mp}  ({size}, {fstype}){marker}")
        else:
            print(f"    📂  Keine Partition gemountet")

        # Modell, Serial, Kapazität aus smartctl -i -j
        rc_i, out_i, _ = run(["smartctl", "-i", "-j", dev], timeout=15)
        if rc_i in (0, 4) and out_i.strip():
            try:
                info = json.loads(out_i)
                model    = info.get("model_name", "")
                serial   = info.get("serial_number", "")
                capacity = info.get("user_capacity", {}).get("bytes", 0)
                rpm      = info.get("rotation_rate", 0)
                drive_type = "HDD" if rpm and rpm > 0 else "SSD/NVMe"
                cap_str  = f"{capacity / 1e9:.0f} GB" if capacity else ""
                if model:
                    print(f"    🏷️   {model}  |  S/N: {serial}  |  {cap_str}  [{drive_type}]")
            except (json.JSONDecodeError, KeyError):
                pass

        rc, out, err = run(["smartctl", "-H", "-A", dev], timeout=20)

        if rc == -1:
            print(f"    ❌ Fehler: {err}")
            continue

        if "Unable to detect device type" in out + err:
            print(f"    ⚠️  Gerätetyp nicht erkannt (USB-Bridge ohne Passthrough?)")
            continue

        # Gesamtstatus
        overall, raw_line = parse_smart_overall(out)
        print(f"    Gesundheit: {overall}")

        # Kritische Attribute
        attrs = parse_smart_attrs(out)
        if attrs:
            crits = [a for a in attrs if a["level"] == "crit"]
            warns = [a for a in attrs if a["level"] == "warn"]

            if crits:
                print(f"    ❌  KRITISCH — Platte möglicherweise am Sterben!")
                for a in crits:
                    print(f"       [{a['id']:>3}] {a['name']:<28}: {a['raw']:>6}  ← SEHR HOCH")
            if warns:
                print(f"    ⚠️   Erhöhte Werte:")
                for a in warns:
                    print(f"       [{a['id']:>3}] {a['name']:<28}: {a['raw']:>6}")
            if not crits and not warns:
                print(f"    ✅  Alle kritischen Attribute unauffällig")

        # NVMe Extra-Infos
        if "nvme" in dev.lower():
            check_nvme_details(dev, out)


# ─────────────────────────────────────────────
# 4. CPU & TEMPERATUREN
# ─────────────────────────────────────────────
def check_cpu():
    header("⚙️  CPU & Auslastung")

    cpu_model = platform.processor()
    if not cpu_model and IS_LINUX:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_model = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
    print(f"  Modell    : {cpu_model or 'Unbekannt'}")
    print(f"  Kerne     : {psutil.cpu_count(logical=False)} physisch / "
          f"{psutil.cpu_count(logical=True)} logisch")

    # Kurze Messung
    usage = psutil.cpu_percent(interval=1, percpu=False)
    status = "✅" if usage < 80 else ("⚠️ " if usage < 95 else "❌")
    print(f"  Auslastung: {usage:.1f}%  {status}")

    # Load Average (nur Linux/macOS)
    if hasattr(os, "getloadavg"):
        la = os.getloadavg()
        cores = psutil.cpu_count(logical=True) or 1
        la_status = "✅" if la[0] / cores < 0.8 else "⚠️ "
        print(f"  Load avg  : {la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f} "
              f"(1/5/15 min)  {la_status}")

    # Temperaturen
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            print(f"  Temperaturen:")
            for chip, readings in temps.items():
                for r in readings:
                    if r.current and r.current > 0:
                        warn = "⚠️ " if r.current > 80 else ("❌" if r.current > 95 else "")
                        label = f"{chip}/{r.label}" if r.label else chip
                        print(f"    {label:<30}: {r.current:.0f}°C  {warn}")
    except (AttributeError, NotImplementedError):
        print(f"  Temperaturen: nicht verfügbar (kein lm-sensors?)")


# ─────────────────────────────────────────────
# 5. UPTIME & SYSTEM-INFO
# ─────────────────────────────────────────────
def get_windows_version() -> str:
    """Liest detaillierte Windows-Version aus der Registry.
    Korrektur: ProductName sagt manchmal noch 'Windows 10' auf Win11-Systemen,
    daher wird ab Build 22000 auf Windows 11 korrigiert."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        def rv(name, default=""):
            try:   return winreg.QueryValueEx(key, name)[0]
            except: return default

        product    = rv("ProductName")
        display_v  = rv("DisplayVersion")
        build      = rv("CurrentBuild")
        ubr        = rv("UBR")
        edition    = rv("EditionID")
        winreg.CloseKey(key)

        # Windows 11 hat Build >= 22000, aber ProductName sagt auf Upgrade-Systemen
        # manchmal noch "Windows 10". Manuell korrigieren:
        try:
            build_int = int(build)
            if build_int >= 22000 and "Windows 10" in product:
                product = product.replace("Windows 10", "Windows 11")
        except (ValueError, TypeError):
            pass

        # Edition aus ProductName ableiten falls nicht in EditionID
        ver_str = product
        if display_v: ver_str += f"  {display_v}"
        if build:     ver_str += f"  (Build {build}.{ubr})"
        return ver_str
    except Exception:
        return f"{platform.system()} {platform.release()} {platform.version()}"


def get_os_install_date() -> str:
    """Ermittelt das OS-Installationsdatum."""
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            # InstallTime ist ein 64-bit FILETIME (100ns-Intervalle seit 1601-01-01)
            try:
                install_time = winreg.QueryValueEx(key, "InstallTime")[0]
                winreg.CloseKey(key)
                # FILETIME → Unix-Timestamp
                unix_ts = (install_time - 116444736000000000) / 10000000
                return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d")
            except:
                # Fallback: InstallDate (Unix-Timestamp als DWORD)
                install_date = winreg.QueryValueEx(key, "InstallDate")[0]
                winreg.CloseKey(key)
                return datetime.fromtimestamp(install_date).strftime("%Y-%m-%d")
        except Exception:
            return "unbekannt"

    elif IS_LINUX:
        # Methode 1: Filesystem-Erstellungsdatum der Root-Partition (ext2/3/4)
        try:
            rc, out2, _ = run(["df", "--output=source", "/"])
            if rc == 0:
                root_dev = out2.strip().splitlines()[-1].strip()
                rc2, out2, _ = run(["tune2fs", "-l", root_dev], timeout=10)
                if rc2 == 0:
                    for line in out2.splitlines():
                        if "Filesystem created" in line:
                            date_str = line.split(":", 1)[1].strip()
                            # Format: "Thu Jan  1 00:00:00 2015"
                            try:
                                dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y")
                                return dt.strftime("%Y-%m-%d")
                            except ValueError:
                                return date_str
        except Exception:
            pass

        # Methode 2: Arch/CachyOS — erste Zeile pacman.log = Bootstrap-Datum
        try:
            with open("/var/log/pacman.log") as f:
                first_line = f.readline().strip()
            # Format: [2023-04-12T14:33:21+0200] [PACMAN] Running 'pacman -r ...'
            m = re.match(r"\[(\d{4}-\d{2}-\d{2})", first_line)
            if m:
                return m.group(1) + "  (pacman.log, Arch-Bootstrap)"
        except OSError:
            pass

        # Methode 3: Installationslog (Debian/Ubuntu)
        for logpath in ["/var/log/installer/syslog", "/var/log/installer/status"]:
            try:
                st = os.stat(logpath)
                return datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d") + "  (Installer-Log)"
            except OSError:
                pass

        # Methode 4: lost+found ctime (grobe Schätzung)
        try:
            st = os.stat("/lost+found")
            return datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d") + "  (geschätzt)"
        except OSError:
            pass

        return "unbekannt"

    return "unbekannt"


def check_system_info():
    header("ℹ️  System")

    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    days = uptime.days
    hours, rem = divmod(uptime.seconds, 3600)
    minutes = rem // 60

    print(f"  Hostname  : {platform.node()}")

    # Lokale IPs — virtuelle/Container-Interfaces rausfiltern
    skip_patterns = re.compile(
        r"^(lo|docker|veth|br-|virbr|vmbr|lxc|lxdbr|tun|tap|dummy|bond|ovs|vlan)"
    )
    ifaces = psutil.net_if_addrs()
    shown_ips = []
    for iface, addrs in sorted(ifaces.items()):
        if skip_patterns.match(iface):
            continue
        for addr in addrs:
            if addr.family.name in ("AF_INET", "AF_INET6"):
                ip = addr.address.split("%")[0]
                if addr.family.name == "AF_INET6" and ip.startswith("fe80"):
                    continue
                shown_ips.append(f"{iface}: {ip}")
    if shown_ips:
        print(f"  IP        : {' | '.join(shown_ips)}")

    # OS-Version — Windows detailliert, Linux normal
    if IS_WINDOWS:
        print(f"  OS        : {get_windows_version()}")
    else:
        cpu_model_os = ""
        try:
            with open("/proc/version") as f:
                # z.B. "Linux version 6.1.0-44-amd64 (debian-kernel@...)"
                pass
        except OSError:
            pass
        print(f"  OS        : {platform.system()} {platform.release()}")

    print(f"  Architektur: {platform.machine()}")
    print(f"  Python    : {platform.python_version()}")
    print(f"  Installiert: {get_os_install_date()}")
    print(f"  Boot-Zeit : {boot.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Uptime    : {days}d {hours}h {minutes}m")


# ─────────────────────────────────────────────
# 6. FESTPLATTENPLATZ
# ─────────────────────────────────────────────
def check_disk_space():
    header("📁  Festplattenplatz")

    # Lokale Partitionen
    partitions = psutil.disk_partitions(all=False)
    printed = set()
    for p in partitions:
        try:
            usage = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue

        key = (p.device, p.mountpoint)
        if key in printed:
            continue
        printed.add(key)

        pct = usage.percent
        status = "✅" if pct < 80 else ("⚠️ " if pct < 92 else "❌")
        free_gb = usage.free / 1024**3
        total_gb = usage.total / 1024**3

        print(f"  {p.mountpoint:<22} {pct:5.1f}% voll  "
              f"({free_gb:.1f} GB frei / {total_gb:.1f} GB)  {status}")

    # Windows: Netzlaufwerke via PowerShell / net use
    if IS_WINDOWS:
        # net use ist universell verfügbar
        rc, out, _ = run(["net", "use"], timeout=10)
        if rc == 0 and out.strip():
            net_lines = []
            for line in out.splitlines():
                # Typisches Format: "OK           Z:        \\server\share   Microsoft Windows Network"
                m = re.match(r"\s*(OK|Verbunden|Connected|Getrennt|Disconnected|Unavail\S*)\s+(\w:)\s+(\\\\\S+)", line, re.IGNORECASE)
                if m:
                    status_str, drive, unc = m.group(1), m.group(2), m.group(3)
                    try:
                        usage = psutil.disk_usage(drive + "\\")
                        pct = usage.percent
                        s = "✅" if pct < 80 else ("⚠️ " if pct < 92 else "❌")
                        free_gb  = usage.free  / 1024**3
                        total_gb = usage.total / 1024**3
                        size_str = f"{pct:.1f}% voll  ({free_gb:.1f} GB frei / {total_gb:.1f} GB)  {s}"
                    except (OSError, PermissionError):
                        size_str = f"({status_str})"
                    net_lines.append(f"  {drive} → {unc:<40} {size_str}")
            if net_lines:
                print(f"\n  Netzlaufwerke:")
                for l in net_lines:
                    print(l)


# ─────────────────────────────────────────────
# 7. DOCKER
# ─────────────────────────────────────────────
def check_docker():
    header("🐳  Docker")

    # Docker erreichbar?
    rc, out, err = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if rc != 0:
        if "permission denied" in err.lower():
            print("  ⚠️  Docker läuft, aber kein Zugriff (Benutzer nicht in docker-Gruppe / kein root)")
        else:
            print("  Docker nicht gefunden oder nicht aktiv.")
        return

    print(f"  Docker Version : {out.strip()}")

    # Container zählen
    rc_run, out_run, _ = run(["docker", "ps",  "-q"])
    rc_all, out_all, _ = run(["docker", "ps", "-aq"])

    running = len([l for l in out_run.strip().splitlines() if l]) if rc_run == 0 else "?"
    total   = len([l for l in out_all.strip().splitlines() if l]) if rc_all == 0 else "?"
    stopped = (total - running) if isinstance(total, int) and isinstance(running, int) else "?"

    print(f"  Container      : {running} laufend  |  {stopped} gestoppt  |  {total} gesamt")

    # Netzwerke & Volumes kurz
    rc_net, out_net, _ = run(["docker", "network", "ls", "-q"])
    rc_vol, out_vol, _ = run(["docker", "volume",  "ls", "-q"])
    nets = len(out_net.strip().splitlines()) if rc_net == 0 else "?"
    vols = len(out_vol.strip().splitlines()) if rc_vol == 0 else "?"
    print(f"  Netzwerke      : {nets}  |  Volumes: {vols}")


# ─────────────────────────────────────────────
# 8. ZRAM / ZFS
# ─────────────────────────────────────────────
def check_storage_extras():
    header("🗄️  Storage-Extras (ZRam / ZFS)")

    # ── ZRam ──────────────────────────────────
    rc, out, _ = run(["zramctl"])
    if rc == 0 and out.strip():
        lines = out.strip().splitlines()
        # Prüfen ob wirklich Devices da sind (nicht nur Header-Zeile)
        if len(lines) > 1:
            print("  ZRam   : ✅ aktiv")
            for line in lines:
                print(f"    {line}")
        else:
            print("  ZRam   : nicht aktiv (keine Devices)")
    else:
        # Fallback: /sys/block
        zram_devs = []
        try:
            zram_devs = [e for e in os.listdir("/sys/block") if e.startswith("zram")]
        except OSError:
            pass
        if zram_devs:
            print(f"  ZRam   : ✅ aktiv ({', '.join(zram_devs)}) — zramctl nicht verfügbar für Details")
        else:
            print("  ZRam   : nicht aktiv")

    # ── ZFS ───────────────────────────────────
    rc, out, _ = run(["zpool", "list", "-H", "-o", "name,size,alloc,free,health"])
    if rc == 0 and out.strip():
        print("  ZFS    : ✅ aktiv")
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                name, size, alloc, free, health = parts[:5]
                icon = "✅" if health == "ONLINE" else ("⚠️ " if health in ("DEGRADED",) else "❌")
                print(f"    Pool '{name}': {size} gesamt / {free} frei — {health} {icon}")
            else:
                print(f"    {line}")
    elif rc == -1:
        print("  ZFS    : nicht installiert")
    else:
        print("  ZFS    : installiert, aber keine Pools aktiv")


# ─────────────────────────────────────────────
# 9. CRYPTO-TOOLS & MOUNTS
# ─────────────────────────────────────────────
def check_tool_present(name: str, version_args: list = None) -> tuple[bool, str]:
    """Prüft ob ein Tool im PATH ist, gibt (vorhanden, version) zurück."""
    args = version_args or ["--version"]
    rc, out, err = run([name] + args)
    if rc == -1:
        return False, ""
    combined = (out + err).strip().splitlines()
    version_line = combined[0][:60] if combined else ""
    return True, version_line


def check_crypto():
    header("🔐  Crypto-Tools & verschlüsselte Mounts")

    # ── Tools installiert? ─────────────────────
    print("  Installierte Crypto-Tools:")
    tools = [
        ("age",          ["--version"],  "age"),
        ("cryfs",        ["--version"],  "CryFS"),
        ("cryptomator",  ["--version"],  "Cryptomator"),
        ("cryptsetup",   ["--version"],  "cryptsetup (LUKS)"),
        ("encfs",        ["--version"],  "EncFS"),
        ("gocryptfs",    ["-version"],   "gocryptfs"),
        ("gpg",          ["--version"],  "GnuPG"),
        ("openssl",      ["version"],    "OpenSSL"),
        ("truecrypt",    ["--version"],  "TrueCrypt"),
        ("veracrypt",    ["--version"],  "VeraCrypt"),
    ]
    tool_entries = []
    for cmd, args, label in tools:
        found, ver = check_tool_present(cmd, args)
        ver_short = ver.split("\n")[0][:50] if found else ""
        tool_entries.append((label, found, ver_short))

    # BitLocker (Windows built-in) — alphabetisch einsortiert
    if IS_WINDOWS:
        import shutil
        if shutil.which("manage-bde"):
            rc_v, out_v, _ = run([
                "powershell", "-NoProfile", "-Command",
                "(Get-Item (Get-Command manage-bde.exe).Source).VersionInfo.ProductVersion"
            ], timeout=5)
            ver_bl = f"v{out_v.strip()}" if rc_v == 0 and out_v.strip() else "Windows built-in"
            tool_entries.append(("BitLocker (manage-bde)", True, ver_bl))
        else:
            tool_entries.append(("BitLocker (manage-bde)", False, ""))

    for label, found, ver in sorted(tool_entries, key=lambda x: x[0].lower()):
        if found:
            print(f"    ✅  {label:<22} {ver}")
        else:
            print(f"    ·   {label}")

    # ── Crypto-Mounts ──────────────────────────
    print("\n  Aktive verschlüsselte Mounts:")
    found_any = False

    # ── Windows: BitLocker ────────────────────
    if IS_WINDOWS:
        rc, out, err = run(["manage-bde", "-status"], timeout=20)
        if rc == 0 and out.strip():
            # Per-Volume Daten sammeln und alle anzeigen (verschlüsselt oder nicht)
            vol_data: dict[str, dict] = {}
            current_vol = None
            for line in out.splitlines():
                ls = line.strip()
                # Volume "C:" [Label] oder Volume C: — Anführungszeichen optional
                m = re.match(r'Volume\s+"?(\w:)', ls, re.IGNORECASE)
                if m:
                    current_vol = m.group(1)
                    vol_data[current_vol] = {}
                    continue
                if current_vol and ":" in ls:
                    key, _, val = ls.partition(":")
                    key, val = key.strip(), val.strip()
                    if re.search(r"Protection Status|Schutzstatus", key, re.I):
                        vol_data[current_vol]["protection"] = val
                    elif re.search(r"Conversion Status|Konvertierungsstatus", key, re.I):
                        vol_data[current_vol]["conversion"] = val
                    elif re.search(r"Encryption Method|Verschl.sselungsmethode", key, re.I):
                        vol_data[current_vol]["method"] = val
            for vol, info in sorted(vol_data.items()):
                prot = info.get("protection", "?")
                conv = info.get("conversion", "?")
                meth = info.get("method", "")
                # Case-insensitiv: "aktiviert", " ein", " on", "aktiv"
                is_on = any(w in prot.lower() for w in ("aktiv", " ein", " on"))
                meth_str = f"  [{meth}]" if meth and meth not in ("Keine", "None", "No") else ""
                if is_on:
                    print(f"    🔒  BitLocker {vol}  — {conv}{meth_str}  (Schutz: {prot})")
                    found_any = True
                else:
                    print(f"    ·   BitLocker {vol}  — {conv}  (Schutz: {prot})")
        elif rc == -1:
            # manage-bde nicht gefunden — mit PowerShell versuchen
            rc2, out2, _ = run([
                "powershell", "-NoProfile", "-Command",
                "try { Get-BitLockerVolume | "
                "Select-Object MountPoint,VolumeStatus,EncryptionMethod,ProtectionStatus | "
                "Format-Table -AutoSize | Out-String -Width 120 } "
                "catch { Write-Output ('FEHLER: ' + $_.Exception.Message) }"
            ], timeout=15)
            if rc2 == 0 and out2.strip() and not out2.strip().startswith("FEHLER"):
                for line in out2.strip().splitlines():
                    ls = line.strip()
                    if not ls or ls.startswith("-") or "MountPoint" in ls:
                        continue
                    is_enc = "FullyDecrypted" not in ls
                    icon = "🔒" if is_enc else "·"
                    print(f"    {icon}  BitLocker      : {ls}")
                    if is_enc:
                        found_any = True
            else:
                print("    ⚠️  BitLocker nicht abfragbar (manage-bde fehlt, Get-BitLockerVolume nicht verfügbar)")
        else:
            if any(w in (out + err).lower() for w in
                   ("access", "verweigert", "administrator", "privilege", "denied")):
                print("    ⚠️  BitLocker-Abfrage verweigert — bitte als Administrator ausführen")
            else:
                print(f"    ⚠️  manage-bde Fehler (exit code {rc}): {(out + err).strip()[:100]}")
    rc, out, _ = run(["dmsetup", "ls", "--target", "crypt"])
    if rc == 0 and out.strip() and "No devices found" not in out:
        for line in out.strip().splitlines():
            name = line.split()[0]
            # Zugehöriges Device und Mountpoint herausfinden
            rc2, out2, _ = run(["dmsetup", "info", "-c", "--noheadings",
                                  "-o", "name,blkdevname,open", name])
            print(f"    🔒  LUKS/dm-crypt : /dev/mapper/{name}")
            found_any = True

    # LUKS via lsblk als Ergänzung
    rc, out, _ = run(["lsblk", "-J", "-o", "NAME,TYPE,MOUNTPOINT"])
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            def walk(devs):
                for d in devs:
                    if d.get("type") == "crypt":
                        mp = d.get("mountpoint") or "(kein Mountpoint)"
                        print(f"    🔒  LUKS          : /dev/{d['name']}  →  {mp}")
                        found_any = True  # nonlocal würde hier besser sein, Workaround:
                    if d.get("children"):
                        walk(d["children"])
            walk(data.get("blockdevices", []))
        except (json.JSONDecodeError, KeyError):
            pass

    # FUSE-basierte Crypto-Filesysteme aus /proc/mounts
    fuse_crypto = {
        "fuse.cryfs":      ("🔒  CryFS",      "CryFS"),
        "fuse.gocryptfs":  ("🔒  gocryptfs",  "gocryptfs"),
        "fuse.encfs":      ("🔒  EncFS",       "EncFS"),
    }
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    fstype = parts[2]
                    if fstype in fuse_crypto:
                        icon, label = fuse_crypto[fstype]
                        src, mp = parts[0], parts[1]
                        print(f"    {icon:<20}: {src}  →  {mp}")
                        found_any = True
    except OSError:
        pass

    # VeraCrypt gemountete Volumes
    rc, out, _ = run(["veracrypt", "--text", "--list"])
    if rc == 0 and out.strip():
        for line in out.strip().splitlines():
            if line.strip():
                print(f"    🔒  VeraCrypt     : {line.strip()}")
                found_any = True

    if not found_any:
        print("    · Keine verschlüsselten Mounts erkannt")


# ─────────────────────────────────────────────
# 11. HARDWARE (NIC & GPU)
# ─────────────────────────────────────────────
def check_hardware():
    header("🔧  Hardware (Netzwerkkarte & Grafik)")

    if IS_LINUX:
        # ── Netzwerkkarten via lspci ───────────────
        print("  Netzwerkkarten:")
        rc, out, _ = run(["lspci", "-mm"])   # -mm = maschinenlesbares Format
        if rc == 0:
            nic_lines = []
            for line in out.splitlines():
                low = line.lower()
                if any(k in low for k in ("ethernet", "network", "wireless",
                                          "wi-fi", "wlan", "802.11")):
                    # Format: "00:1f.6" "Ethernet controller" "Intel Corporation" "I219-V" ...
                    parts = re.findall(r'"([^"]*)"', line)
                    if len(parts) >= 4:
                        vendor, device = parts[2], parts[3]
                        nic_lines.append(f"    🌐  {vendor} — {device}")
                    elif len(parts) >= 2:
                        nic_lines.append(f"    🌐  {' — '.join(parts[1:3])}")
            if nic_lines:
                for l in nic_lines: print(l)
            else:
                # Fallback: rohe lspci-Ausgabe
                rc2, out2, _ = run(["lspci"])
                for line in out2.splitlines():
                    if any(k in line.lower() for k in
                           ("ethernet", "network controller", "wireless")):
                        print(f"    🌐  {line.split(':', 2)[-1].strip()}")
        else:
            print("    ⚠️  lspci nicht gefunden (pciutils installieren?)")

        # Zusatz: Interface ↔ Treiber aus /sys
        skip = re.compile(r"^(lo|docker|veth|br-|virbr|vmbr|lxc|lxdbr|dummy|bond|ovs|vlan)")
        for iface in sorted(os.listdir("/sys/class/net")):
            if skip.match(iface):
                continue
            driver_path = f"/sys/class/net/{iface}/device/driver"
            try:
                driver = os.path.basename(os.readlink(driver_path))
                speed_path = f"/sys/class/net/{iface}/speed"
                try:
                    speed = open(speed_path).read().strip()
                    speed_str = f"  {speed} Mbit/s" if speed != "-1" else ""
                except OSError:
                    speed_str = ""
                print(f"    ↳  {iface:<12} Treiber: {driver}{speed_str}")
            except OSError:
                pass

        # ── GPU via lspci ──────────────────────────
        print("\n  Grafikkarte(n):")
        if rc == 0:
            gpu_lines = []
            for line in out.splitlines():
                low = line.lower()
                if any(k in low for k in ("vga", "display", "3d controller",
                                          "video controller")):
                    parts = re.findall(r'"([^"]*)"', line)
                    if len(parts) >= 4:
                        vendor, device = parts[2], parts[3]
                        gpu_lines.append((vendor, device))
                    elif parts:
                        gpu_lines.append(("", parts[-1]))
            for vendor, device in gpu_lines:
                print(f"    🖥️   {vendor} — {device}")

        # NVIDIA: nvidia-smi für Details
        rc_n, out_n, _ = run([
            "nvidia-smi",
            "--query-gpu=name,memory.total,temperature.gpu,utilization.gpu,driver_version",
            "--format=csv,noheader,nounits"
        ])
        if rc_n == 0 and out_n.strip():
            for line in out_n.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    name, vram, temp, util, drv = parts[:5]
                    print(f"    ↳  NVIDIA {name}")
                    print(f"       VRAM: {int(vram):,} MB  |  Temp: {temp}°C  "
                          f"|  Auslastung: {util}%  |  Treiber: {drv}")

        # AMD: rocm-smi oder /sys
        rc_a, out_a, _ = run(["rocm-smi", "--showproductname", "--showtemp",
                               "--showmeminfo", "vram", "--csv"])
        if rc_a == 0 and out_a.strip():
            lines = [l for l in out_a.strip().splitlines() if l.strip()]
            if len(lines) > 1:
                print(f"    ↳  AMD (rocm-smi):")
                for l in lines[1:]:
                    print(f"       {l.strip()}")
        else:
            # Fallback: VRAM-Größe aus /sys/class/drm
            for card in sorted(os.listdir("/sys/class/drm")):
                if not re.match(r"^card\d+$", card):
                    continue
                vendor_path = f"/sys/class/drm/{card}/device/vendor"
                mem_path    = f"/sys/class/drm/{card}/device/mem_info_vram_total"
                try:
                    vendor = open(vendor_path).read().strip()
                    if vendor == "0x1002":  # AMD
                        try:
                            vram_b = int(open(mem_path).read().strip())
                            print(f"    ↳  {card}: AMD  |  VRAM: {vram_b // 1024**2:,} MB")
                        except OSError:
                            print(f"    ↳  {card}: AMD")
                except OSError:
                    pass

        if not gpu_lines:
            # Letzter Fallback
            rc3, out3, _ = run(["lspci"])
            for line in out3.splitlines():
                if any(k in line.lower() for k in ("vga", "display", "3d")):
                    print(f"    🖥️   {line.split(':', 2)[-1].strip()}")

    elif IS_WINDOWS:
        # ── Netzwerkkarten ─────────────────────────
        print("  Netzwerkkarten:")
        rc, out, _ = run([
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_NetworkAdapter | "
            "Where-Object {$_.PhysicalAdapter -eq $true} | "
            "Select-Object Name,MACAddress,Speed | "
            "Format-Table -AutoSize | Out-String -Width 120"
        ], timeout=15)
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                line = line.strip()
                if line and not line.startswith("---") and "Name" not in line:
                    print(f"    🌐  {line}")
        else:
            print("    ⚠️  Keine Info verfügbar")

        # ── GPU ────────────────────────────────────
        print("\n  Grafikkarte(n):")
        rc, out, _ = run([
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor | "
            "Format-List | Out-String -Width 120"
        ], timeout=15)
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                # AdapterRAM in GB umrechnen
                m = re.match(r"AdapterRAM\s*:\s*(\d+)", line)
                if m:
                    vram_gb = int(m.group(1)) / 1024**3
                    print(f"    🖥️   AdapterRAM      : {vram_gb:.0f} GB")
                else:
                    print(f"    🖥️   {line}")
        else:
            print("    ⚠️  Keine Info verfügbar")
def get_ntp_offset(server: str = "pool.ntp.org", timeout: int = 5) -> float | None:
    """Fragt einen NTP-Server ab und gibt den Offset in Sekunden zurück."""
    import socket
    import struct
    import time as _time

    try:
        # NTP-Request: LI=0, VN=3, Mode=3 (client)
        packet = b'\x1b' + 47 * b'\0'
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        t_send = _time.time()
        sock.sendto(packet, (server, 123))
        data, _ = sock.recvfrom(1024)
        t_recv = _time.time()
        sock.close()

        # Transmit Timestamp: Bytes 40-47 (high 32 bit = Sekunden seit 1900-01-01)
        if len(data) < 48:
            return None
        ntp_secs = struct.unpack('!I', data[40:44])[0]
        ntp_frac = struct.unpack('!I', data[44:48])[0]
        ntp_time = ntp_secs - 2208988800 + ntp_frac / 2**32  # → Unix-Zeit

        # Einfache Offset-Schätzung: NTP-Zeit vs. Mitte des Round-Trips
        rtt    = t_recv - t_send
        offset = ntp_time - (t_send + rtt / 2)
        return offset
    except Exception:
        return None


def check_timesync():
    header("🕐  Zeitabgleich & Zeitzone")

    import time as _time

    # Zeitzone
    now_local = datetime.now().astimezone()
    tz_name   = now_local.strftime("%Z")          # z.B. "CEST"
    tz_offset = now_local.strftime("%z")          # z.B. "+0200"
    # Lesbarere Offset-Darstellung: +0200 → UTC+2:00
    try:
        sign   = "+" if tz_offset[0] != "-" else "-"
        h_off  = int(tz_offset[1:3])
        m_off  = int(tz_offset[3:5])
        tz_readable = f"UTC{sign}{h_off}:{m_off:02d}" if m_off else f"UTC{sign}{h_off}"
    except (ValueError, IndexError):
        tz_readable = tz_offset

    print(f"  Systemzeit : {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Zeitzone   : {tz_name}  ({tz_readable})")

    # Linux: timedatectl für NTP-Sync-Status
    if IS_LINUX:
        rc, out, _ = run(["timedatectl", "show", "--no-pager"])
        if rc == 0:
            fields = {}
            for line in out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    fields[k.strip()] = v.strip()
            ntp_active  = fields.get("NTP", "no").lower() == "yes"
            ntp_sync    = fields.get("NTPSynchronized", "no").lower() == "yes"
            ntp_icon    = "✅" if ntp_sync else "❌"
            ntp_service = fields.get("NTPService", "")
            print(f"  NTP aktiv  : {'ja' if ntp_active else 'nein'}  "
                  f"| Synchronisiert: {'ja' if ntp_sync else 'NEIN'}  {ntp_icon}")
            if ntp_service:
                print(f"  NTP-Dienst : {ntp_service}")

    # Windows: w32tm
    elif IS_WINDOWS:
        rc, out, _ = run(["w32tm", "/query", "/status"], timeout=10)
        if rc == 0:
            for line in out.strip().splitlines():
                if any(k in line for k in ("Source", "Stratum", "Last", "Quelle", "Schicht")):
                    print(f"  {line.strip()}")

    # NTP-Offset messen (unabhängig vom OS)
    ntp_servers = ["pool.ntp.org", "time.cloudflare.com", "time.google.com"]
    offset = None
    used_server = ""
    for srv in ntp_servers:
        o = get_ntp_offset(srv)
        if o is not None:
            offset = o
            used_server = srv
            break

    if offset is not None:
        abs_off = abs(offset)
        if abs_off < 0.5:
            icon = "✅"
            verdict = "gut"
        elif abs_off < 5:
            icon = "⚠️ "
            verdict = "leicht abweichend"
        elif abs_off < 60:
            icon = "⚠️ "
            verdict = "deutlich abweichend — NTP prüfen!"
        else:
            icon = "❌"
            verdict = f"GROSSE ABWEICHUNG — NTP defekt oder falsche Zeitzone?"

        direction = "vor" if offset > 0 else "nach"
        print(f"  NTP-Offset : {offset:+.3f}s  ({abs_off:.1f}s {direction})"
              f"  {icon}  {verdict}")
        print(f"  NTP-Server : {used_server}")
    else:
        print(f"  NTP-Offset : ⚠️  Kein NTP-Server erreichbar (Firewall? UDP 123?)")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{'═'*60}")
    print(f"  SYSTEM HEALTH CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*60}")

    # Root-Warnung
    if IS_LINUX and os.geteuid() != 0:
        print("\n  ⚠️  Nicht als root — SMART/NVMe-Abfragen könnten scheitern.")
        print("       Empfohlen: sudo python3 syshealth.py\n")

    check_system_info()
    check_timesync()
    check_vm()
    check_ram()
    check_cpu()
    check_disk_space()
    check_smart()
    check_hardware()
    check_docker()
    check_storage_extras()
    check_crypto()

    print(f"\n{'═'*60}")
    print(f"  Check abgeschlossen.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
