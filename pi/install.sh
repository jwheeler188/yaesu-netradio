#!/usr/bin/env bash
# W5PL FT-897D network radio - install the bridge on the Raspberry Pi the Digirig is plugged into.
#   ./install.sh                     defaults: CAT 38400 baud, PTT on RTS (Digirig), CAT TCP 4533, audio TCP 4534
#   ./install.sh --baud 9600         extra options are passed to ft897-netbridge.py (see --help)
#   ./install.sh --remove            remove the service again
# Radio: menu 019 CAT RATE must equal --baud (default 38400). Log: journalctl -u ft897-netbridge -f
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT=/etc/systemd/system/ft897-netbridge.service
RULE=/etc/udev/rules.d/90-digirig-no-modemmanager.rules
say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

if [[ "${1:-}" == "--remove" ]]; then
  sudo systemctl disable --now ft897-netbridge 2>/dev/null || true
  sudo rm -f "$UNIT" "$RULE"; sudo systemctl daemon-reload; sudo udevadm control --reload
  say "FT-897D bridge service removed"; exit 0
fi
[[ -f "$HERE/ft897-netbridge.py" && -f "$HERE/ft897_access.py" && -f "$HERE/ft897_lsb.py" && -f "$HERE/ft897_tap.py" && -f "$HERE/ft897_radio.py" ]] || { echo "ft897-netbridge.py not found next to this script"; exit 1; }

say "Installing Hamlib (rigctld), ALSA tools and pyserial"
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q libhamlib-utils alsa-utils python3-serial
rigctld --version

# Winlink on the Pi (optional): always when something was put in ../winlink/, otherwise asked once xara isn't installed yet
wl=""
if ls "$HERE/../winlink/"xara* "$HERE/../winlink/"pat_*.deb >/dev/null 2>&1; then wl=1
elif [[ ! -x "$HOME/.local/bin/xara" && -t 0 ]]; then
  read -r -p "Also set up Winlink email on this Pi (xara + Pat, about 5 minutes)? [y/N] " a; [[ "$a" == [yY]* ]] && wl=1
fi
if [[ -n "$wl" ]]; then bash "$HERE/winlink-install.sh" || say "Winlink setup did not finish (see above); the radio bridge is installed anyway"
else say "Winlink email on the Pi: run pi/winlink-install.sh whenever you like"; fi

# ModemManager probes new USB serial ports with AT commands; keep it away from the Digirig's CP2102 (ea60) and the FT-891's CP2105 (ea70)
say "Telling ModemManager to leave the Digirig serial port alone ($RULE)"
printf '%s\n' 'ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", ENV{ID_MM_DEVICE_IGNORE}="1"' 'ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea70", ENV{ID_MM_DEVICE_IGNORE}="1"' | sudo tee "$RULE" >/dev/null
sudo udevadm control --reload; sudo udevadm trigger --subsystem-match=tty || true

say "Installing the service ($UNIT) for user $USER"
sudo tee "$UNIT" >/dev/null <<UNITFILE
[Unit]
Description=W5PL FT-897D network bridge (CAT TCP 4533, audio TCP 4534)
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$USER
SupplementaryGroups=dialout audio
ExecStart=/usr/bin/python3 -u $HERE/ft897-netbridge.py -v $*
KillMode=mixed
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITFILE
sudo systemctl daemon-reload
sudo systemctl enable ft897-netbridge
sudo systemctl restart ft897-netbridge
sleep 3
journalctl -u ft897-netbridge -n 12 --no-pager -o cat
if command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  say "Firewall (ufw) is on: allowing TCP 4533, 4534 and 8080"; for t in 4533 4534 8080; do sudo ufw allow $t/tcp; done
fi
say "Done. Laptop: Hamlib NET rigctl $(hostname).local:4533, and run ft897-audio.py --server $(hostname).local"
