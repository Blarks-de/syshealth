# 🏥 syshealth.py

Ein umfassendes System-Monitoring-Tool für Linux und Windows (macOS-Support in Arbeit).

## Was kann das Ding?

- **VM-Erkennung**: Checkt, ob du auf Bare-Metal oder in einer VM läufst (VMware, VirtualBox, KVM, Hyper-V, etc.)
- **RAM-Status**: Speicherauslastung, Swap, Dirty Pages (Linux)
- **SMART-Status**: Gesundheit deiner HDDs und SSDs
- **NVMe-Health**: Wear Level, Temperature, Critical Warnings für NVMe-Drives
- **CPU & Temperaturen**: Load, Core-Temps, CPU-Info
- **Festplattenplatz**: Freier Speicher, Mount-Points
- **Docker**: Container-Status und Images (falls installiert)
- **Hardware-Info**: Netzwerkkarten, GPUs (NVIDIA/AMD)
- **Verschlüsselung**: LUKS-Status und BitLocker (Windows)
- **Zeitabgleich**: NTP-Offset, Zeitzone, Sync-Status

## Installation

```bash
# Python 3.10+ erforderlich
pip install psutil

# Linux: smartmontools installieren
sudo apt install smartmontools      # Debian/Ubuntu
sudo dnf install smartmontools      # Fedora/RHEL
sudo pacman -S smartmontools         # Arch

# Windows: smartmontools für Windows oder CrystalDiskInfo CLI
```

## Benutzung

```bash
# Linux (mit Root-Rechten für SMART/NVMe)
sudo python3 syshealth.py

# Windows (als Administrator in PowerShell)
python syshealth.py
```

## Bekannte Einschränkungen

- **macOS**: Noch nicht implementiert (kommt bald™)
- **SMART unter Windows**: Braucht externe Tools oder Admin-Rechte
- **NVMe unter Windows**: Funktioniert nur mit `nvme-cli` (nicht standardmäßig installiert)

## TODO

- [ ] macOS-Support (`platform.system() == "Darwin"`)
- [ ] JSON-Output-Option für Monitoring-Integration
- [ ] Config-File für Schwellwerte
- [ ] Optionale Benachrichtigungen (Mail/Webhook)

## Lizenz

MIT (oder was auch immer du willst — ist dein Code)

## Warum?

Weil `htop` und Task-Manager nicht genug Nerd-Punkte geben.

---

## 🤖 AI-Generated Code

**English:**  
This project is 100% AI-generated code, created through prompt engineering with Claude (Anthropic). No manual coding was involved — just a human with ideas and an AI that doesn't need coffee breaks. Proof that good prompts beat good programmers. *"One good prompter achieves more than ten coders."*

**Deutsch:**  
Dieses Projekt ist zu 100% AI-generierter Code, entstanden durch Prompt-Engineering mit Claude (Anthropic). Kein manuelles Coding involviert — nur ein Mensch mit Ideen und eine KI, die keine Kaffeepausen braucht. Der Beweis, dass gute Prompts besser sind als gute Programmierer. *"Ein Antreiber schafft mehr wie 10 Arbeiter :-)"*

### How It Was Made / Wie es entstand

- **Prompt:** "Build me a system health monitoring tool for Linux and Windows"
- **Claude:** *cracks knuckles* "Hold my tokens..."
- **Result:** 1400+ lines of production-ready Python

No Stack Overflow copy-paste. No trial-and-error debugging sessions at 3 AM. Just conversational programming. Welcome to 2025.
