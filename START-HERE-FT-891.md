# FT-891 test version

Runs a Yaesu FT-891 from a web page, with your voice and the receive audio going through your laptop's headset.

**This has never been on a real FT-891.** It was tested only against a simulated one, with Hamlib 4.5.5. Please go through the steps in order, with a
dummy load and 5 W first, someone beside the radio, and a hand near its power switch. Private test copy: please don't pass it on yet.

## What you need

- A Raspberry Pi (4 or 5) with Raspberry Pi OS or Ubuntu, on the same network as your laptop, and a user that can use `sudo`.
- A Digirig **DR-891**, or another sound interface on the rear RTTY/DATA jack, plugged into the Pi.
- The FT-891's own **USB** socket connected to the Pi (the DR-891 does this with its red cable).
- A Mac or Windows laptop with a headset.

## 1. Radio menus

Hold **F** for one second, turn **MULTI** to choose, press it, turn, press it again, then **F**. The numbers are from the 2017 manual;
early firmware counts a few differently, so go by the name.

| Menu | Set to | Why |
|---|---|---|
| **05-14 TX TOT** | **3 min** | The radio stops transmitting by itself if everything else fails. **Do not skip this** (05-11 in early firmware) |
| 05-06 CAT RATE | 38400 | Any speed works, it is found automatically |
| 05-07 CAT TOT | 1000 ms | Digirig's setting |
| 05-08 CAT RTS | DISABLE | |
| 05-19 APO | OFF | Or the radio switches itself off |
| 07-12 PC KEYING | OFF | |
| **11-05 SSB MIC SELECT** | **REAR** | Voice from the rear jack on USB and LSB. **The hand mic is off while this is REAR** |
| 06-05 AM MIC SELECT, 09-01 FM MIC SELECT | REAR | Only if you use AM or FM |
| 08-01 DATA MODE | OTHERS | FT8 and other data modes |
| 08-09 DATA IN SELECT | REAR | |
| 08-12 DATA BFO | USB | |
| 08-10, 11-08, 06-07, 09-03 (PTT SELECT) | DAKY | **Not RTS or DTR:** the radio could key when a program opens the port |
| HF / 50M power | **5 W** | For the first test |

Filter: press F, turn MULTI to **WDH** and open it to about 2.4-3.0 kHz. **Set MIC SELECT back to MIC when you want the hand mic again.**

## 2. The Raspberry Pi

1. Copy this folder to the Pi's home folder.
2. Open a terminal on the Pi and install:
   ```bash
   cd ~/FT891-Remote-Test/pi && ./install.sh --radio ft891
   ```
3. Read what it prints. Good signs: `FT-891 found on ... at 38400 baud`, `radio answering: 14.2500 MHz`, `RX audio from plughw:...`.
4. Find the Pi's address:
   ```bash
   hostname -I
   ```

## 3. The laptop

- **Mac:** run `macOS/install.sh` once (needs [Homebrew](https://brew.sh)). Then double-click **macOS/Start FT-897 Voice.command**. If macOS refuses,
  right-click it and choose Open. (The files still carry the FT-897 name.)
- **Windows:** install Python 3 (x64, "Add python.exe to PATH"), then double-click **windows/Start FT-897 Voice.bat**.

The first time, it asks for the Pi's address. It then opens the panel, `http://PI-ADDRESS:8080`. The title must say **FT-891 Remote**.
Under **Admin**, set an administrator password, then an operator password: until then anyone on your network can transmit.

## 4. Receive test (nothing is transmitted)

Please note yes or no for each:

1. The frequency and mode on the panel are the radio's, and follow the radio's dial.
2. Band buttons, every mode button, and tuning by scrolling a digit change the radio.
3. You hear the receiver. Does turning the radio's AF volume or squelch knob change the sound in your headset?
4. The waterfall moves. The S-meter agrees with the radio's, roughly.
5. RIT +100 / Clear, A ⇄ B, Split, Lock and the **Power** slider each do what they say **on the radio's display**.
6. Are there ticks in the audio? If so, do they stop when "Live S-meter" is off?
7. Admin > Sound card levels: is the receive audio clean, not distorted?
8. Switch the radio off: within about 20 seconds the panel must say it is not answering. Switch it on again: the panel must recover.

Stop here and report if 1 or 8 fails.

## 5. Transmit test: dummy load, 5 W, then 20 W

Drive starts at -5; Admin limits how high it goes. Leave the radio's own meter on ALC.

1. **Key once for one second without speaking.** The radio must go to transmit and back. The panel must show no red warning.
2. Hold PTT and talk, 5 seconds. Power and ALC move on the panel? Set Drive so that ALC only just moves.
3. Compare the panel's SWR with the radio's. On a dummy load it should say about 1.0.
4. Hold PTT and **close the browser tab**: the radio must stop transmitting within about 2 seconds.
5. Hold PTT and **stop the voice client** (close its window): it must stop within about 2 seconds.
6. Hold PTT and **restart the bridge**: it must stop within about 5 seconds.
   ```bash
   sudo systemctl restart ft897-netbridge
   ```
7. Admin: lock 20 m, then press PTT on 20 m: refused. Unlock it.
8. Split on with VFO B outside the band: PTT must be refused.
9. Only if you can do it safely: **pull the radio's USB cable while keyed at 5 W.** The panel should warn, and the radio must stop by itself at the TX TOT time.
   Nothing but TX TOT can un-key it then. Switch the radio off if it doesn't.

Do not try to raise the SWR on purpose. The high-SWR shutdown (step 7 of 15, about 2.4:1 by Hamlib's table for this family of radios) is not proven on this radio.

## 6. On the air

Dummy load passed? Antenna, 20-50 W, a clear frequency, your call. Listen to yourself on a WebSDR. On LSB and USB, is the voice natural and not mirrored?

## 7. What to send back

The answers above, the radio's firmware version, which interface you have, which Pi system, and this file (it has your local network addresses and computer names in it):
```bash
journalctl -u ft897-netbridge --since today --no-pager -o cat > ~/ft891-log.txt
```

## Known limits

- **PTT goes by CAT** (the command TX1), so a lost USB cable or a Pi that loses power leaves the radio keyed until TX TOT. A crash of the bridge alone is caught
  when it restarts, about 5 seconds later. A hardware PTT line (RTS on a Digirig Mobile's own port, into pin 3 DAKY) lets go by itself:
  `./install.sh --radio ft891 --ptt RTS --ptt-serial /dev/serial/by-id/...`. Not tested.
- The repeater offset is read from the radio (menus RPT SHIFT 28MHz / 50MHz), not set. DCS works only for both directions (DSQL).
- The panel's FM squelch was tuned on an FT-897D. Use the radio's own squelch if it doesn't suit.
- "Remove CAT ticks" was made for the FT-897D and may do nothing here.
- FT8 with WSJT-X: Rig "Hamlib NET rigctl", server PI-ADDRESS:4533, PTT CAT, mode Data/Pkt, split "None" or "Fake It". Hamlib copies VFO A to VFO B
  whenever such a program changes the mode. Audio: `Start FT-897 Audio` with BlackHole 2ch (Mac) or VB-CABLE (Windows).
- CAT 4533 and audio 4534 have no password. Use it on your own network or a VPN only. Never forward these ports to the internet.

Remove everything from the Pi:
```bash
cd ~/FT891-Remote-Test/pi && ./install.sh --remove
```
