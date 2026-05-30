# 🏥 syshealth.py

Ein umfassendes System-Monitoring-Tool für Linux, Windows und macOS.

## Was kann das Ding?

- **VM-Erkennung**: Checkt, ob du auf Bare-Metal oder in einer VM läufst (VMware, VirtualBox, KVM, Hyper-V, Parallels, UTM, etc.)
- **RAM-Status**: Speicherauslastung, Swap, Dirty Pages (Linux)
- **SMART-Status**: Gesundheit deiner HDDs und SSDs
- **NVMe-Health**: Wear Level, Temperature, Critical Warnings für NVMe-Drives
- **CPU & Temperaturen**: Load, Core-Temps, CPU-Info
- **Festplattenplatz**: Freier Speicher, Mount-Points
- **Docker**: Container-Status und Images (falls installiert)
- **Hardware-Info**: Netzwerkkarten, GPUs (NVIDIA/AMD/Apple Silicon)
- **Hardware-Alter**: Produktionsdatum aus Serial Number (macOS) — wann wurde dein Mac gebaut?
- **Verschlüsselung**: LUKS-Status und BitLocker (Windows)
- **Zeitabgleich**: NTP-Offset, Zeitzone, Sync-Status
- **Apple Time Machine**: Backup-Status über SMB-Share — wann war das letzte Backup, läuft es gerade, ist es intakt?
- **Sicherheitscheck**: Warnt automatisch wenn `.env` nicht im `.gitignore` steht

## Installation

```bash
# Python 3.8+ erforderlich (3.10+ empfohlen)
pip install psutil

# Linux: smartmontools und lm-sensors installieren
sudo apt install smartmontools lm-sensors  # Debian/Ubuntu
sudo dnf install smartmontools lm_sensors  # Fedora/RHEL
sudo pacman -S smartmontools lm_sensors    # Arch

# Linux: cifs-utils für Time Machine SMB-Check
sudo apt install cifs-utils   # Debian/Ubuntu
sudo dnf install cifs-utils   # Fedora/RHEL
sudo pacman -S cifs-utils     # Arch

# macOS: Homebrew-Tools installieren
brew install smartmontools      # Für SMART-Checks
brew install osx-cpu-temp       # Optional: CPU-Temperaturen

# Windows: smartmontools für Windows oder CrystalDiskInfo CLI
```

## Time Machine Konfiguration

Für den Backup-Check eine `.env` Datei **neben `syshealth.py`** anlegen:

```ini
TM_HOST=Dockfish          # Hostname oder IP deines NAS
TM_SHARE=TimeMachine      # Name der SMB-Freigabe
TM_USER=backup            # SMB-Benutzername
TM_PASS=geheimesPasswort  # SMB-Passwort
# TM_MACHINE=MeinMacBook  # Optional: filtert nach Maschinenname
```

> ⚠️ **Wichtig:** `.env` unbedingt in `.gitignore` eintragen!
> ```bash
> echo ".env" >> .gitignore
> ```
> syshealth.py warnt automatisch, wenn das vergessen wurde.

Beispiel-Output:
```
  Status     : ✅  Intakt
  Letztes    : 24. Mai 2026 14:00 Uhr  (3 Stunden alt)
  Maschine   : MacBook-von-Mark
  Ziel       : //Dockfish/TimeMachine
```

Auf **macOS** wird `tmutil` verwendet (kein Mount, kein Root nötig) — sofern Time Machine auf diesem Mac konfiguriert ist. Auf **Linux** wird der SMB-Share kurz gemountet (Root nötig, `cifs-utils` vorausgesetzt).

## Benutzung

```bash
# Linux (mit Root-Rechten für SMART/NVMe)
sudo python3 syshealth.py

# macOS (mit sudo für SMART-Zugriff)
sudo python3 syshealth.py

# Windows (als Administrator in PowerShell)
python syshealth.py
```

## Features im Detail

### macOS-Spezifisch
- **Hardware-Alter**: Dekodiert Serial Number → Produktionsjahr + Alter in Jahren
- **VM-Erkennung**: Erkennt Parallels, VMware Fusion, UTM, VirtualBox
- **SMART via diskutil**: Fallback wenn smartmontools nicht installiert
- **System-Info**: sw_vers für macOS-Version + Build-Nummer
- **Time Machine**: Nutzt `tmutil` direkt — kein Mount, kein Root nötig

### Linux-Spezifisch
- **Dirty Pages**: Warnung bei ungeschriebenem Cache (>2% RAM)
- **VM-Erkennung**: systemd-detect-virt, DMI, Hypervisor-Flags, Kernel-Module
- **GPU-Details**: NVIDIA (nvidia-smi), AMD (rocm-smi), Intel

### Windows-Spezifisch
- **BitLocker-Status**: Verschlüsselte Laufwerke
- **Native SMART-Checks**: PowerShell-Fallback ohne smartctl

## Bekannte Einschränkungen

- **SMART unter Windows**: Braucht externe Tools oder Admin-Rechte
- **NVMe unter Windows**: Funktioniert nur mit `nvme-cli` (nicht standardmäßig installiert)
- **Temperaturen macOS**: Benötigt `osx-cpu-temp` (via Homebrew)
- **Temperaturen Linux**: Benötigt `lm-sensors`
- **Hardware-Alter**: Nur macOS (Serial Number Dekodierung)

## Kompatibilität

- **Python**: 3.8 bis 3.14+ (Type Hints für alte Versionen angepasst)
- **Betriebssysteme**: Linux, Windows 10/11, macOS 10.15+
- **Architekturen**: x86_64, ARM64 (Apple Silicon getestet)

## TODO

- [ ] JSON-Output-Option für Monitoring-Integration
- [ ] Config-File für Schwellwerte
- [ ] Optionale Benachrichtigungen (Mail/Webhook)
- [ ] Time Machine: Mehrere Backups / Maschinen in einem Report

## Lizenz

MIT (oder was auch immer du willst — ist dein Code)

## Warum?

Weil `htop` und Task-Manager nicht genug Nerd-Punkte geben.

---

## 🤖 AI-Generated Code

**English:**  
This project is 100% AI-generated code, created through prompt engineering with Claude (Anthropic). No manual coding was involved — just a human with ideas and an AI that doesn't need coffee breaks. Proof that good prompts beat good programmers. *"One good prompter achieves more than ten coders."*

**Deutsch:**  
Dieses Projekt ist zu 100% AI-generierter Code, entstanden durch Prompt-Engineering mit Claude (Anthropic). Kein manuelles Coding involviert — nur ein Mensch mit Ideen und eine KI, die keine Kaffeepausen braucht. Der Beweis, dass gute Prompts besser sind als gute Programmierer. *"Ein Antreiber schafft mehr wie 10 Arbeiter."*

### How It Was Made / Wie es entstand

- **Prompt:** "Build me a system health monitoring tool for Linux and Windows"
- **Claude:** *cracks knuckles* "Hold my tokens..."
- **Result:** 1400+ lines of production-ready Python

No Stack Overflow copy-paste. No trial-and-error debugging sessions at 3 AM. Just conversational programming. Welcome to 2025.
