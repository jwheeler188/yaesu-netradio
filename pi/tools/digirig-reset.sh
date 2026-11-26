#!/bin/bash
# Unplugs the Digirig from the Pi in software and plugs it back in (resets its USB hub, serial port and sound card).
#   sudo bash digirig-reset.sh
# Only the Digirig is on this hub. It never touches the radio's power.
[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }
S=$(for d in /sys/bus/usb/devices/*; do [ -f "$d/idVendor" ] && [ "$(cat "$d/idVendor"):$(cat "$d/idProduct")" = "10c4:ea60" ] && basename "$d"; done | head -1)
[ -n "$S" ] || { echo "no Digirig serial port (10c4:ea60) found"; exit 1; }
HUB=${S%.*}; H=/sys/bus/usb/devices/$HUB
[ "$(cat $H/idVendor):$(cat $H/idProduct)" = "0424:2412" ] || { echo "$HUB is not the Digirig's own hub ($(cat $H/idVendor):$(cat $H/idProduct)): not touching it"; exit 1; }
trap 'echo 1 > $H/authorized 2>/dev/null; systemctl start ft897-netbridge' EXIT
echo "stopping the bridge (un-keys the radio first)"; systemctl stop ft897-netbridge; sleep 1
echo "unplugging $HUB"; echo 0 > $H/authorized; sleep 5
echo "plugging it back in"; echo 1 > $H/authorized; sleep 4
for i in $(seq 1 10); do [ -e /dev/serial/by-id ] && ls /dev/serial/by-id | grep -q CP210 && break; sleep 1; done
ls /dev/serial/by-id; arecord -l | grep -i usb
trap - EXIT; systemctl start ft897-netbridge; sleep 6; journalctl -u ft897-netbridge -n 8 --no-pager -o cat
