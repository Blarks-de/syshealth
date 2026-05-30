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
  
  Linux: 
    - smartmontools (smartctl)
    - lm-sensors (für CPU-Temperaturen)
  
  Windows: 
    - smartmontools für Windows oder CrystalDiskInfo CLI
  
  macOS:
    - smartmontools (via Homebrew: brew install smartmontools)
    - osx-cpu-temp (optional, für CPU-Temperaturen: brew install osx-cpu-temp)
"""

import subprocess
import platform
import sys
import os
import json
import re
import plistlib
import tempfile
import urllib.parse
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

try:
    import psutil
except ImportError:
    print("[!] psutil fehlt. Bitte installieren: pip install psutil")
    sys.exit(1)

__version__ = "0.5.0"
__version_date__ = "30.05.2026"

SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == "windows"
IS_LINUX   = SYSTEM == "linux"
IS_MACOS   = SYSTEM == "darwin"

SEP = "─" * 60


def run(cmd: list, timeout: int = 10) -> Tuple[int, str, str]:
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
def detect_vm() -> Tuple[bool, str]:
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

    elif IS_MACOS:
        # system_profiler für Hardware-Info
        rc, out, _ = run(["system_profiler", "SPHardwareDataType"], timeout=15)
        if rc == 0:
            vm_keywords = ["vmware", "virtualbox", "parallels", "virtual", "qemu", "utm"]
            for kw in vm_keywords:
                if kw in out.lower():
                    # Extrahiere relevante Zeile
                    for line in out.splitlines():
                        if "model" in line.lower() and kw in line.lower():
                            hints.append(f"Hardware: {line.strip()[:80]}")
                            break
                    if not hints:
                        hints.append(f"system_profiler: VM-Keyword '{kw}' gefunden")
                    break
        
        # sysctl für Hypervisor-Check (funktioniert bei manchen VMs)
        rc, out, _ = run(["sysctl", "-n", "machdep.cpu.features"])
        if rc == 0 and "hypervisor" in out.lower():
            hints.append("sysctl: Hypervisor-Feature erkannt")
        
        # ioreg für VM-spezifische Geräte
        rc, out, _ = run(["ioreg", "-l"], timeout=10)
        if rc == 0:
            vm_devices = ["VMware", "VirtualBox", "Parallels", "QEMU"]
            for dev in vm_devices:
                if dev in out:
                    hints.append(f"ioreg: {dev}-Device gefunden")
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
def get_block_devices() -> List[str]:
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
    elif IS_MACOS:
        # diskutil list gibt alle Disks aus
        rc, out, _ = run(["diskutil", "list"])
        if rc == 0:
            for line in out.splitlines():
                # Zeilen wie "/dev/disk0" oder "/dev/disk1 (internal, physical)"
                match = re.match(r"^(/dev/disk\d+)\s+\(.*physical.*\)", line)
                if match:
                    devices.append(match.group(1))
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


def parse_smart_overall(output: str) -> Tuple[str, str]:
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


def parse_smart_attrs(output: str) -> List[dict]:
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

    # macOS-Pfad
    if IS_MACOS:
        devices = get_block_devices()
        if not devices:
            print("  Keine Blockgeräte gefunden.")
            return

        for dev in devices:
            print(f"\n  Gerät: {dev}")
            
            # diskutil info für grundlegende Infos
            rc_di, out_di, _ = run(["diskutil", "info", dev])
            if rc_di == 0:
                model = serial = ""
                for line in out_di.splitlines():
                    if "Device / Media Name:" in line:
                        model = line.split(":", 1)[-1].strip()
                    elif "Disk Size:" in line:
                        size = line.split(":", 1)[-1].strip()
                        print(f"    📦  {model or dev}  |  Größe: {size}")
                        break
            
            # Versuche smartctl (wenn via Homebrew installiert)
            rc, out, err = run(["smartctl", "-H", "-A", dev], timeout=20)
            
            if rc == -1 or "command not found" in err.lower():
                # Kein smartctl → nutze diskutil verifyDisk als Fallback
                print(f"    ⚠️  smartctl nicht verfügbar (installiere mit: brew install smartmontools)")
                rc_v, out_v, _ = run(["diskutil", "verifyDisk", dev])
                if rc_v == 0 and "appears to be OK" in out_v:
                    print(f"    ✅  Disk Verification: OK (kein vollständiger SMART-Check)")
                elif rc_v == 0:
                    print(f"    ⚠️  Disk Verification: {out_v.strip()[:100]}")
                continue
            
            # smartctl verfügbar
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
            
            # NVMe auf macOS (oft über Apple Silicon oder Thunderbolt)
            if "nvme" in out.lower() or "apple" in out.lower():
                check_nvme_details(dev, out)
        return  # macOS-Pfad abgeschlossen

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
    elif not cpu_model and IS_MACOS:
        # macOS: sysctl für CPU-Modell
        rc, out, _ = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if rc == 0 and out.strip() and out.strip() != "arm":
            cpu_model = out.strip()
        else:
            # Fallback für Apple Silicon: hw.model
            rc2, out2, _ = run(["sysctl", "-n", "hw.model"])
            if rc2 == 0 and out2.strip():
                cpu_model = out2.strip()
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
        elif IS_MACOS:
            # macOS: Versuche osx-cpu-temp (mehrere Pfade, da sudo anderen PATH hat)
            temp_found = False
            for cmd in [
                "osx-cpu-temp",                    # Im PATH
                "/opt/homebrew/bin/osx-cpu-temp",  # Homebrew auf Apple Silicon
                "/usr/local/bin/osx-cpu-temp"      # Homebrew auf Intel
            ]:
                rc, out, _ = run([cmd, "-c"])
                if rc == 0 and out.strip():
                    try:
                        temp = float(out.strip())
                        warn = "⚠️ " if temp > 80 else ("❌" if temp > 95 else "")
                        print(f"  Temperaturen:")
                        print(f"    CPU                           : {temp:.0f}°C  {warn}")
                        temp_found = True
                        break
                    except ValueError:
                        pass
            if not temp_found:
                print(f"  Temperaturen: nicht verfügbar (installiere 'osx-cpu-temp' via brew)")
    except (AttributeError, NotImplementedError):
        if IS_LINUX:
            print(f"  Temperaturen: nicht verfügbar (kein lm-sensors?)")
        elif IS_MACOS:
            print(f"  Temperaturen: nicht verfügbar (installiere 'osx-cpu-temp' via brew)")


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


def get_hardware_age() -> str:
    """Ermittelt das Hardware-Alter (nur macOS via Serial Number)."""
    if not IS_MACOS:
        return ""
    
    try:
        rc, out, _ = run(["system_profiler", "SPHardwareDataType"], timeout=10)
        if rc != 0:
            return ""
        
        serial = model_id = ""
        for line in out.splitlines():
            if "Serial Number" in line:
                serial = line.split(":", 1)[1].strip()
            elif "Model Identifier" in line:
                model_id = line.split(":", 1)[1].strip()
        
        if not serial or not model_id:
            return ""
        
        # Serial Number dekodieren
        # Alte Format (vor 2021): 12 Zeichen, z.B. C02ABCDEFGH1
        # Neue Format (ab 2021): 10 Zeichen, z.B. ABCD123456
        production_year = production_info = ""
        
        if len(serial) == 12:
            # Altes Format: Position 4-5 = Jahr/Woche
            # Position 4: Jahr (9=2019, 0=2020, C=2012, D=2013, F=2014, G=2015, H=2016, J=2017, K=2018, L=2019, M=2020, N=2021, P=2022, Q=2023, R=2024, etc.)
            year_code = serial[3]
            week_code = serial[4]
            year_map = {
                'C': 2012, 'D': 2013, 'F': 2014, 'G': 2015, 'H': 2016,
                'J': 2017, 'K': 2018, 'L': 2019, 'M': 2020, 'N': 2021,
                'P': 2022, 'Q': 2023, 'R': 2024, 'S': 2025, 'T': 2026,
                'V': 2027, 'W': 2028, 'X': 2029, 'Y': 2030, 'Z': 2031
            }
            if year_code in year_map:
                production_year = str(year_map[year_code])
                production_info = f"produziert ca. {production_year}"
        elif len(serial) == 10:
            # Neues Format (ab 2021): Position 4 = Jahr (halbjährlich)
            # Die Codes starten bei C=2020 H2 und gehen alphabetisch weiter
            # ABER: überspringt E, I, O, U (wie Vokale)
            year_code = serial[3]
            year_map_new = {
                'C': '2020 H2', 'D': '2021 H1', 'F': '2021 H2', 'G': '2022 H1',
                'H': '2022 H2', 'J': '2023 H1', 'K': '2023 H2', 'L': '2024 H1',
                'M': '2024 H2', 'N': '2025 H1', 'P': '2025 H2', 'Q': '2026 H1',
                'R': '2026 H2', 'S': '2027 H1', 'T': '2027 H2', 'V': '2028 H1',
                'W': '2028 H2', 'X': '2029 H1', 'Y': '2029 H2', 'Z': '2030 H1'
            }
            if year_code in year_map_new:
                production_info = f"produziert ca. {year_map_new[year_code]}"
                production_year = year_map_new[year_code].split()[0]
            else:
                # Fallback: wenn Code unbekannt, einfach anzeigen
                production_info = f"Seriennummer: {serial} (Code: {year_code} unbekannt)"
        
        # Alter berechnen
        age_str = ""
        if production_year:
            try:
                prod_year_int = int(production_year)
                current_year = datetime.now().year
                age = current_year - prod_year_int
                if age == 0:
                    age_str = " — <1 Jahr alt"
                elif age == 1:
                    age_str = " — ca. 1 Jahr alt"
                else:
                    age_str = f" — ca. {age} Jahre alt"
            except ValueError:
                pass
        
        result = f"{model_id}"
        if production_info:
            result += f" ({production_info}{age_str})"
        
        return result
        
    except Exception:
        return ""


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
    
    elif IS_MACOS:
        # Methode 1: .AppleSetupDone = wann Setup-Assistent abgeschlossen wurde
        try:
            st = os.stat("/var/db/.AppleSetupDone")
            return datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d") + "  (Setup-Assistent)"
        except OSError:
            pass
        
        # Methode 2: /var/log/install.log (ältester Eintrag)
        try:
            st = os.stat("/var/log/install.log")
            return datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d") + "  (Install-Log)"
        except OSError:
            pass
        
        # Methode 3: älteste Systemdatei im Root
        try:
            oldest = None
            for item in ["/private", "/System", "/Applications"]:
                try:
                    st = os.stat(item)
                    if oldest is None or st.st_birthtime < oldest:
                        oldest = st.st_birthtime
                except (OSError, AttributeError):
                    pass
            if oldest:
                return datetime.fromtimestamp(oldest).strftime("%Y-%m-%d") + "  (geschätzt)"
        except Exception:
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

    # OS-Version — Windows detailliert, Linux normal, macOS mit sw_vers
    if IS_WINDOWS:
        print(f"  OS        : {get_windows_version()}")
    elif IS_MACOS:
        rc, out, _ = run(["sw_vers"])
        if rc == 0:
            # Ausgabe wie: "ProductName: macOS\nProductVersion: 14.2\n..."
            version_info = {}
            for line in out.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    version_info[k.strip()] = v.strip()
            prod_name = version_info.get("ProductName", "macOS")
            prod_ver = version_info.get("ProductVersion", "")
            build = version_info.get("BuildVersion", "")
            print(f"  OS        : {prod_name} {prod_ver} (Build: {build})")
        else:
            print(f"  OS        : macOS (sw_vers nicht verfügbar)")
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
    
    # Hardware-Alter (nur macOS)
    if IS_MACOS:
        hw_age = get_hardware_age()
        if hw_age:
            print(f"  Hardware  : {hw_age}")
    
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
def check_tool_present(name: str, version_args: list = None) -> Tuple[bool, str]:
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
            for card in sorted(os.listdir("/sys/class/drm")) if os.path.isdir("/sys/class/drm") else []:
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
    
    elif IS_MACOS:
        # ── Netzwerkkarten ─────────────────────────
        print("  Netzwerkkarten:")
        rc, out, _ = run(["networksetup", "-listallhardwareports"], timeout=10)
        if rc == 0 and out.strip():
            current_port = None
            current_device = None
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Hardware Port:"):
                    current_port = line.split(":", 1)[1].strip()
                elif line.startswith("Device:"):
                    current_device = line.split(":", 1)[1].strip()
                    if current_port and current_device:
                        # Hole MAC und Status
                        rc_if, out_if, _ = run(["ifconfig", current_device])
                        mac = status = ""
                        if rc_if == 0:
                            for ifline in out_if.splitlines():
                                if "ether" in ifline:
                                    mac = ifline.split()[1]
                                elif "status:" in ifline:
                                    status = ifline.split(":", 1)[1].strip()
                        status_icon = "🟢" if status == "active" else "⚪"
                        mac_str = f" | MAC: {mac}" if mac else ""
                        print(f"    🌐  {current_port:<30} ({current_device}) {status_icon}{mac_str}")
                        current_port = current_device = None
        else:
            # Fallback: ifconfig
            rc2, out2, _ = run(["ifconfig"])
            if rc2 == 0:
                skip = re.compile(r"^(lo|gif|stf|ap|awdl|llw|utun|bridge)")
                for line in out2.splitlines():
                    if re.match(r"^\w+:", line):
                        iface = line.split(":")[0]
                        if not skip.match(iface):
                            print(f"    🌐  {iface}")

        # ── GPU via system_profiler ────────────────
        print("\n  Grafikkarte(n):")
        rc, out, _ = run(["system_profiler", "SPDisplaysDataType"], timeout=15)
        if rc == 0 and out.strip():
            gpu_name = vram = ""
            for line in out.splitlines():
                line = line.strip()
                if "Chipset Model:" in line:
                    gpu_name = line.split(":", 1)[1].strip()
                elif "VRAM" in line or "Metal" in line:
                    # Kann sein: "VRAM (Total): 8 GB" oder "Metal Family: Supported, Metal GPUFamily Apple 7"
                    if ":" in line:
                        vram = line.split(":", 1)[1].strip()
                        if gpu_name and vram:
                            # VRAM kann auch "Built-In" oder "Shared" sein
                            print(f"    🖥️   {gpu_name}")
                            if "GB" in vram or "MB" in vram:
                                print(f"         VRAM: {vram}")
                            gpu_name = vram = ""
            # Falls noch ein GPU-Name übrig ist (z.B. letzte GPU ohne VRAM-Info)
            if gpu_name:
                print(f"    🖥️   {gpu_name}")
        else:
            print("    ⚠️  system_profiler fehlgeschlagen")

def get_ntp_offset(server: str = "pool.ntp.org", timeout: int = 5) -> Optional[float]:
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
# APPLE TIME MACHINE BACKUP CHECK
# ─────────────────────────────────────────────

def _tm_load_env() -> dict:
    """
    Liest TM_* Variablen aus .env (neben syshealth.py).
    Erwartete Einträge:
        TM_HOST=Dockfish          # Hostname oder IP des NAS
        TM_SHARE=TimeMachine      # SMB-Freigabe-Name
        TM_USER=backup            # SMB-Benutzer
        TM_PASS=geheim            # SMB-Passwort
        TM_MACHINE=MeinMacBook    # optional: Maschinenname-Filter
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    cfg = {}
    if not os.path.exists(env_path):
        return cfg
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key.startswith("TM_"):
                cfg[key] = val
    return cfg


def _tm_format_age(delta: timedelta) -> str:
    """timedelta → lesbarer deutscher String."""
    secs = int(delta.total_seconds())
    if secs < 0:
        return "aus der Zukunft (?)"
    if secs < 3600:
        m = secs // 60
        return f"{m} Minute{'n' if m != 1 else ''}"
    if secs < 86400:
        h = secs // 3600
        return f"{h} Stunde{'n' if h != 1 else ''}"
    d = secs // 86400
    return f"{d} Tag{'e' if d != 1 else ''}"


def _tm_format_dt(dt: datetime) -> str:
    """datetime → '24. Mai 2026 14:00 Uhr'"""
    months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    return f"{dt.day}. {months[dt.month]} {dt.year} {dt.strftime('%H:%M')} Uhr"


def _tm_via_tmutil() -> Optional[dict]:
    """
    macOS only: tmutil für direkten Status-Abruf (kein Mount nötig).
    Gibt dict oder None zurück.
    """
    try:
        raw    = subprocess.check_output(
            ["tmutil", "status", "-X"], stderr=subprocess.DEVNULL, timeout=10
        )
        status = plistlib.loads(raw)
    except Exception:
        return None

    result   = {"running": bool(status.get("Running", 0)),
                "phase":   status.get("BackupPhase", "")}
    progress = status.get("Progress", {})
    if result["running"] and isinstance(progress, dict):
        pct = progress.get("Percent", -1)
        if isinstance(pct, (int, float)) and pct >= 0:
            result["progress_pct"] = round(float(pct) * 100, 1)

    # Letztes Backup-Datum via latestbackup
    try:
        latest = subprocess.check_output(
            ["tmutil", "latestbackup"], stderr=subprocess.DEVNULL,
            timeout=10, text=True
        ).strip()
        m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{6})$", latest)
        if m:
            result["last_backup"] = datetime.strptime(
                f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H%M%S"
            )
            result["machine"] = os.path.basename(os.path.dirname(latest))
    except Exception:
        pass

    # Destination-Name (NAS-Hostname)
    try:
        dest_raw = subprocess.check_output(
            ["tmutil", "destinationinfo", "-X"],
            stderr=subprocess.DEVNULL, timeout=5
        )
        dest_info = plistlib.loads(dest_raw)
        dests = dest_info.get("Destinations", [dest_info])
        if dests:
            d = dests[0]
            result["dest_name"] = d.get("Name", d.get("VolumeName", ""))
    except Exception:
        pass

    return result


def _tm_mount_smb(host: str, share: str, user: str, password: str,
                  mount_point: str) -> Tuple[bool, str]:
    """Mounted SMB-Share temporär. Gibt (Erfolg, Fehlermeldung) zurück."""
    try:
        if IS_MACOS:
            pw_enc = urllib.parse.quote(password, safe="")
            us_enc = urllib.parse.quote(user,     safe="")
            cmd    = ["mount_smbfs",
                      f"//{us_enc}:{pw_enc}@{host}/{share}",
                      mount_point]
        elif IS_LINUX:
            # Benötigt: sudo apt install cifs-utils
            cmd = [
                "mount", "-t", "cifs",
                f"//{host}/{share}", mount_point,
                "-o", (f"username={user},password={password},"
                       f"uid={os.getuid()},nobrl,vers=3.0,sec=ntlmssp")
            ]
        else:
            return False, "Plattform nicht unterstützt"

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout or "Mount fehlgeschlagen").strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout beim Verbinden mit //{host}/{share}"
    except Exception as e:
        return False, str(e)


def _tm_parse_share(mount_point: str, machine_filter: str = "") -> dict:
    """
    Analysiert einen gemounteten TM-Share.
    Unterstützt .sparsebundle (Netzwerk) und Backups.backupdb (älteres Format).
    """
    result = {}
    try:
        entries = os.listdir(mount_point)
    except PermissionError:
        return {"error": "Kein Lesezugriff auf den Share"}

    # ── .sparsebundle (modernes Netzwerk-Backup) ──────────────
    bundles = [e for e in entries if e.endswith(".sparsebundle")]
    if machine_filter:
        filtered = [b for b in bundles if machine_filter.lower() in b.lower()]
        if filtered:
            bundles = filtered

    if bundles:
        bundle_path = os.path.join(mount_point, bundles[0])
        result["type"]    = "sparsebundle"
        result["machine"] = bundles[0].replace(".sparsebundle", "")

        # Läuft gerade?
        try:
            bents = os.listdir(bundle_path)
            result["running"] = (
                any(e.endswith(".inProgress") for e in bents) or
                os.path.exists(os.path.join(bundle_path, ".inProgress"))
            )
        except Exception:
            result["running"] = False

        # Results.plist → letztes Datum + Exit-Code
        results_plist = os.path.join(
            bundle_path, "com.apple.TimeMachine.Results.plist"
        )
        if os.path.exists(results_plist):
            try:
                with open(results_plist, "rb") as f:
                    data = plistlib.load(f)
                dates = data.get("SnapshotDates", [])
                if dates:
                    result["last_backup"] = max(
                        d if isinstance(d, datetime)
                        else datetime.fromisoformat(str(d))
                        for d in dates
                    )
                exit_code = data.get("com.apple.backupd.BackupExit",
                            data.get("Result", None))
                result["backup_ok"] = exit_code in (0, "0", "Success", "success",
                                                     None)
            except Exception:
                pass

        # Fallback: mtime des Bundles
        if "last_backup" not in result:
            try:
                mtime = os.path.getmtime(bundle_path)
                result["last_backup"]        = datetime.fromtimestamp(mtime)
                result["last_backup_approx"] = True
                result["backup_ok"]          = True
            except Exception:
                pass
        return result

    # ── Backups.backupdb (älteres Format) ────────────────────
    if "Backups.backupdb" in entries:
        db_path  = os.path.join(mount_point, "Backups.backupdb")
        result["type"] = "backupdb"
        try:
            machines = [m for m in os.listdir(db_path) if not m.startswith(".")]
            if machine_filter:
                filtered = [m for m in machines
                            if machine_filter.lower() in m.lower()]
                if filtered:
                    machines = filtered
            if machines:
                result["machine"] = machines[0]
                snaps = sorted([s for s in os.listdir(
                    os.path.join(db_path, machines[0])
                ) if not s.startswith(".")])
                if snaps:
                    last = snaps[-1]
                    result["running"] = last.endswith(".inProgress")
                    try:
                        result["last_backup"] = datetime.strptime(
                            last.replace(".inProgress", ""), "%Y-%m-%d-%H%M%S"
                        )
                        result["backup_ok"] = True
                    except ValueError:
                        pass
        except Exception as e:
            result["error"] = str(e)
        return result

    return {}


def _tm_check_cifs_utils() -> bool:
    """Prüft ob cifs-utils auf Linux installiert ist."""
    rc, _, _ = run(["mount.cifs", "--version"])
    if rc >= 0:
        return True
    # Alternativ: Im PATH suchen
    return any(
        os.path.exists(os.path.join(p, "mount.cifs"))
        for p in os.environ.get("PATH", "").split(":")
    )


def check_timemachine():
    """Time Machine Backup-Status über SMB prüfen."""
    header("🍎  Apple Time Machine Backup")

    cfg = _tm_load_env()

    # Nicht konfiguriert → still überspringen
    if not cfg:
        print("  (nicht konfiguriert — .env mit TM_HOST/TM_SHARE/TM_USER/TM_PASS anlegen)")
        return

    # Unvollständige Konfiguration
    required = ["TM_HOST", "TM_SHARE", "TM_USER", "TM_PASS"]
    missing  = [k for k in required if k not in cfg]
    if missing:
        print(f"  ⚠️   .env unvollständig — fehlt: {', '.join(missing)}")
        return

    host    = cfg["TM_HOST"]
    share   = cfg["TM_SHARE"]
    user    = cfg["TM_USER"]
    password= cfg["TM_PASS"]
    machine = cfg.get("TM_MACHINE", "")

    # ── macOS: tmutil zuerst (kein Mount, kein Root nötig) ───
    if IS_MACOS:
        tm = _tm_via_tmutil()
        if tm is not None:
            dest = tm.get("dest_name", host) or host
            if tm["running"]:
                phase = tm.get("phase", "")
                pct   = tm.get("progress_pct", -1)
                phase_str = f"  [{phase}]" if phase else ""
                pct_str   = f"  —  {pct}%" if pct >= 0 else ""
                print(f"  Status     : 🔄  Backup läuft gerade{phase_str}{pct_str}")
                print(f"  Ziel       : //{dest}")
                return
            if "last_backup" in tm:
                dt   = tm["last_backup"]
                age  = datetime.now() - dt
                mname = tm.get("machine", dest)
                print(f"  Status     : ✅  Intakt")
                print(f"  Letztes    : {_tm_format_dt(dt)}  ({_tm_format_age(age)} alt)")
                print(f"  Maschine   : {mname}")
                print(f"  Ziel       : //{dest}")
                return
            print(f"  Status     : ⚠️   tmutil: kein Datum lesbar (noch nie gesichert?)")
            return

    # ── Linux: cifs-utils prüfen ─────────────────────────────
    if IS_LINUX and not _tm_check_cifs_utils():
        print(f"  ❌  cifs-utils nicht installiert — SMB-Mount nicht möglich.")
        print(f"      Bitte installieren:")
        print(f"        Debian/Ubuntu :  sudo apt install cifs-utils")
        print(f"        Fedora/RHEL   :  sudo dnf install cifs-utils")
        print(f"        Arch          :  sudo pacman -S cifs-utils")
        return

    # ── SMB-Mount + Analyse ───────────────────────────────────
    mount_point = tempfile.mkdtemp(prefix="syshealth_tm_")
    mounted     = False

    try:
        print(f"  Verbinde   : //{host}/{share} …", end="", flush=True)
        ok, err = _tm_mount_smb(host, share, user, password, mount_point)

        if not ok:
            print(f"\r  Status     : ❌  //{host}/{share} nicht erreichbar")
            print(f"  Fehler     : {err}")
            return

        mounted     = True
        print(f"\r  Verbinde   : //{host}/{share}  ✓        ")

        backup_info = _tm_parse_share(mount_point, machine)

        if not backup_info:
            print(f"  Status     : ⚠️   Kein TM-Backup auf //{host}/{share} gefunden")
            return

        if "error" in backup_info:
            print(f"  Status     : ❌  Lesefehler: {backup_info['error']}")
            return

        mname   = backup_info.get("machine", host)
        btype   = backup_info.get("type", "")
        approx  = backup_info.get("last_backup_approx", False)

        if backup_info.get("running"):
            print(f"  Status     : 🔄  Backup läuft gerade")
            print(f"  Maschine   : {mname}")
            return

        if "last_backup" in backup_info:
            dt      = backup_info["last_backup"]
            age     = datetime.now() - dt
            ok_flag = backup_info.get("backup_ok", True)
            approx_hint = "  (Datum ca.)" if approx else ""

            if ok_flag:
                print(f"  Status     : ✅  Intakt")
            else:
                print(f"  Status     : ⚠️   Letzter Backup FEHLGESCHLAGEN")

            print(f"  Letztes    : {_tm_format_dt(dt)}{approx_hint}  ({_tm_format_age(age)} alt)")
            print(f"  Maschine   : {mname}")
            print(f"  Format     : {btype}")
            print(f"  Ziel       : //{host}/{share}")
        else:
            print(f"  Status     : ⚠️   Backup gefunden, aber kein Datum lesbar")
            print(f"  Maschine   : {mname}")

    except Exception as e:
        print(f"\n  Status     : ❌  Unerwarteter Fehler: {e}")
    finally:
        if mounted:
            subprocess.run(["umount", mount_point],
                           capture_output=True, timeout=10)
        try:
            os.rmdir(mount_point)
        except Exception:
            pass


# ─────────────────────────────────────────────
# .GITIGNORE SICHERHEITSCHECK
# ─────────────────────────────────────────────

def check_gitignore_safety():
    """
    Warnt wenn .env nicht in .gitignore eingetragen ist.
    Läuft nur wenn ein .git-Verzeichnis vorhanden ist (also wir in einem Repo sind).
    """
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    git_dir     = os.path.join(script_dir, ".git")
    env_file    = os.path.join(script_dir, ".env")
    gitignore   = os.path.join(script_dir, ".gitignore")

    # Kein Repo → irrelevant
    if not os.path.isdir(git_dir):
        return

    # .env existiert nicht → nichts zu schützen
    if not os.path.exists(env_file):
        return

    # .gitignore existiert nicht
    if not os.path.exists(gitignore):
        print(f"\n{'!'*60}")
        print(f"  🚨  SICHERHEITSWARNUNG")
        print(f"{'!'*60}")
        print(f"  .env gefunden, aber KEINE .gitignore!")
        print(f"  Dein NAS-Passwort könnte ins Repo gepusht werden.")
        print(f"  Fix:  echo '.env' >> .gitignore")
        print(f"{'!'*60}")
        return

    # .gitignore vorhanden, aber .env nicht drin
    with open(gitignore, "r") as f:
        lines = [l.strip() for l in f.readlines()]

    # Typische Muster die .env abdecken: ".env", "*.env", ".env*"
    covered = any(
        pat in lines
        for pat in [".env", "*.env", ".env*", "**/.env"]
    )

    if not covered:
        print(f"\n{'!'*60}")
        print(f"  🚨  SICHERHEITSWARNUNG")
        print(f"{'!'*60}")
        print(f"  .env existiert, ist aber NICHT in .gitignore!")
        print(f"  Dein NAS-Passwort könnte ins Repo gepusht werden.")
        print(f"  Fix:  echo '.env' >> .gitignore")
        print(f"{'!'*60}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{'═'*60}")
    print(f"  SYSTEM HEALTH CHECK v{__version__} vom {__version_date__} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*60}")

    # .gitignore Sicherheitscheck (zuerst, damit Warnung nicht untergeht)
    check_gitignore_safety()

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
    check_timemachine()

    print(f"\n{'═'*60}")
    print(f"  Check abgeschlossen.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
