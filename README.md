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
