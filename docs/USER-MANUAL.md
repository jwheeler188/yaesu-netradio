# yaesu-netradio user manual

Remote operation of a Yaesu FT-897D (and, untested, FT-891) through a Raspberry Pi and a Digirig: WSJT-X and other
programs over the network, and voice from a web page with a headset.

> ## ⚠️ Beta software: use at your own risk
>
> This project is **beta** and still being tested. It has run one FT-897D, on one Raspberry Pi 4, with one Digirig,
> from Mac laptops. The FT-891 support and Windows voice have **not** been tried on real hardware yet.
>
> **Remote operation is at your own risk.** The software can key your transmitter. A bug, a network fault or a radio
> fault can leave you unable to see or stop what the radio is doing. You remain the control operator and are
> responsible for every transmission, for identifying, and for following your licence's rules on remote control. Before
> you rely on it remotely, have a way to switch the radio's power off from where you are (see
> [Safety](#9-safety-what-the-software-does-and-what-it-cant)).
>
> **One lock-up has happened** (see [Known issues](#12-known-issues-and-limitations)). The radio stopped answering
> and had to be power-cycled at the radio. The most likely cause is CAT polling while transmitting, but that isn't
> proven. The polling has been reduced, and checks have been added that notice a radio that stops answering.

---

## Contents

1. [What it does](#1-what-it-does)
2. [What you need](#2-what-you-need)
3. [Installing](#3-installing)
4. [First-day setup and calibration](#4-first-day-setup-and-calibration)
5. [Operating from the web panel](#5-operating-from-the-web-panel)
6. [Audio settings reference](#6-audio-settings-reference)
7. [Administration page reference](#7-administration-page-reference)
8. [Command-line options](#8-command-line-options)
9. [Safety: what the software does, and what it can't](#9-safety-what-the-software-does-and-what-it-cant)
10. [Troubleshooting](#10-troubleshooting)
11. [Testing](#11-testing)
12. [Known issues and limitations](#12-known-issues-and-limitations)
13. [Yaesu CAT documentation](#13-yaesu-cat-documentation)
14. [Files, updating and removing](#14-files-updating-and-removing)

---

## 1. What it does

The radio and Digirig stay at home, plugged into a Raspberry Pi. From any computer on your network or VPN you get:

- **Audio processing:** noise reduction (a standard method and a neural one), automatic and manual notch, levelling
  (AGC), bass and treble, voice squelch, a software FM squelch, and removal of the clicks that CAT data leaks into the
  audio. On transmit: microphone levelling, compression, a clarity boost, filters and a limiter. The limiter keeps
  your peaks at the drive level you set.
- **Filtering:** the receive passband is set by sliders, preset buttons, or by dragging its edges in the waterfall.
- **Waterfall and spectrum** of the receive audio. Point at a signal to read its frequency, and double-click to notch
  a whistle.
- **Voice auto-tuning ("Tune in voice"):** it listens to an SSB voice and moves the dial until the voice sounds right.
- **Voice scan and band scope:** it steps through the voice segments you pick, stops on a voice and tunes it in.
- **"LSB voice"** on the FT-897D: real LSB on the air, even though the radio only takes rear-jack audio in its USB
  data mode.
- **Repeaters and favorites:** one click sets frequency, mode, shift, offset and tone, plus your audio settings if
  you like.
- **Sharing:** several people can watch. Only one has control at a time, with hand-over requests and optional
  passwords.
- **Safety un-keys** for lost audio, lost connections, timeouts, high SWR, locked bands and a radio that stops
  answering.
- **WSJT-X, VARA and Pat** use the radio through the standard Hamlib network protocol.

## 2. What you need

| Item | Notes |
|---|---|
| Yaesu FT-897D (or FT-897) | FT-891: see the README's FT-891 section; not yet tested on a real radio |
| Digirig (Mobile) and its FT-897 cables | It provides CAT, audio, and PTT on RTS |
| Raspberry Pi 4 (or similar) | Tested with Ubuntu 24.04; Raspberry Pi OS should work. Wired Ethernet or a clean Wi-Fi link |
| A Mac or Windows laptop | Mac: Homebrew, Python 3, and BlackHole 2ch for WSJT-X. Windows: Python 3 x64 and VB-Audio Virtual Cable |
| A headset | For voice. Wired is best; Bluetooth works but adds delay |
| A VPN for use away from home | WireGuard or similar. **Never** forward these ports to the internet |
| A way to cut the radio's power remotely | Strongly recommended, e.g. a smart switch on the DC supply with the radio's internal batteries removed |

## 3. Installing

### 3.1 The radio (FT-897D)

| Menu | Setting | Why |
|---|---|---|
| 019 CAT RATE | **38400** (or match `--baud`) | Must equal the bridge's baud rate. Power-cycle the radio after changing it |
| 038 DIG MODE | **USER-U** | The panel's DIG = USB data. The 897 only takes rear DATA-jack audio in DIG/PKT modes |
| AGC, NB, DSP, IPO, ATT | as you like | These have no CAT command, so set them on the front panel before you leave |
| Squelch / RF gain knob | **RF gain fully clockwise** for remote use | A closed squelch mutes the rear jack, and there's no CAT command to open it. Use the panel's FM squelch instead |
| Internal batteries | **out** for unattended use | With them in, the radio stays on when you cut the DC |

### 3.2 The Raspberry Pi

1. Plug the Digirig's USB cable into the Pi.
2. Get the project onto the Pi: `git clone https://github.com/jwheeler188/yaesu-netradio`
3. Install it. The installer asks for your sudo password, installs Hamlib, alsa-utils and pyserial, and sets up the
   `ft897-netbridge` service:
   ```bash
   cd ~/yaesu-netradio/pi && ./install.sh
   ```
   If your cable keys the radio by CAT instead of RTS: `./install.sh --ptt CAT`. Any option from
   [section 8](#8-command-line-options) can be added.
4. Check that it found the radio:
   ```bash
   journalctl -u ft897-netbridge -n 30 --no-pager -o cat
   ```
   You should see `radio answering: 14.0740 MHz` (or your frequency).

### 3.3 Mac

1. Install [Homebrew](https://brew.sh), then BlackHole 2ch (`brew install --cask blackhole-2ch`), then restart.
2. Run `macOS/install.sh` once.
3. For WSJT-X and other data modes, double-click **macOS/Start FT-897 Audio.command**. For voice, double-click
   **macOS/Start FT-897 Voice.command**. The first start asks for the Pi's address and saves it in `server.txt`.
4. macOS may refuse to open the files the first time: right-click › Open.

### 3.4 Windows

1. Install Python 3 **x64** from python.org and tick "Add python.exe to PATH". Use x64 even on ARM PCs.
2. Install VB-Audio Virtual Cable (vb-audio.com/Cable) and restart.
3. Double-click **windows/Start FT-897 Audio.bat** (data) or **windows/Start FT-897 Voice.bat** (voice). The first
   start installs PyAudio and asks for the Pi's address.
4. Windows voice is untested, and neural noise reduction isn't available on Windows yet.

### 3.5 WSJT-X, VARA, Pat

- WSJT-X › Settings › Radio: Rig **Hamlib NET rigctl**, Network Server **`<Pi address>`:4533**, PTT **CAT**, Mode
  **Data/Pkt**. Audio: Mac **BlackHole 2ch** in and out; Windows Input **CABLE Output**, Output **CABLE Input**.
- VARA (via xara or similar): `--ptt rigctld:<Pi address>:4533`. Pat: rig address `<Pi address>:4533`.
- Only one audio client runs at a time: the data client (WSJT-X) or the voice client.

## 4. First-day setup and calibration

Do these once, at the radio if you can, into a **dummy load** or at low power.

1. **Administrator password.** Open `http://<Pi address>:8080/admin`. The first visit sets the administrator password.
   Use one you don't use anywhere else, because the page isn't encrypted on your network.
2. **Bands.** On the administration page, lock the bands your antenna can't handle, and check the band edges against
   your licence. The defaults are the US band edges.
3. **Pi sound levels** (administration page, *Sound levels on the Pi*). Starting points measured on the author's
   radio: **Receive (Mic capture) 12** gives about −30 dBFS of receive noise with no clipping. **Transmit (Speaker)
   2**: the ALC starts at about −10 dBFS of app audio. The bridge sets these at every start, and again whenever the
   Digirig is plugged back in (the Digirig resets itself to very loud defaults).
4. **Transmit drive.** Key into a dummy load with the voice client's test tone or WSJT-X and watch the panel's ALC
   meter. Set **Drive** so the ALC just moves on voice peaks. On the author's radio that was −5 dBFS for FM and −4 to
   −3 for SSB. In WSJT-X, set Pwr so there's no ALC (about −12 dB).
5. **FM squelch.** Tune to an empty FM channel and raise **FM squelch** until the hiss is muted (8 is a good start;
   below 6 it never closes). The thresholds assume Mic capture 12, so recheck if you change that.
6. **Operator password** (optional, recommended if others can reach the Pi). Taking control then asks for it.
7. **Check your on-air audio** with a friend, a web SDR, or your repeater's audio monitor page if it has one.

## 5. Operating from the web panel

Open `http://<Pi address>:8080` in any browser: Mac, Windows, phone.

### 5.1 Top bar

- **Take control / Release.** This gives your computer the radio. If someone else has it, they're asked to hand it
  over. With no answer in 30 s you get it (configurable). The administrator can take over at once, which ends their
  transmission. Control is released after 2 minutes without contact.
- **Shared** lists who is connected. **Admin** opens the administration page.

### 5.2 Frequency, mode, VFO

- **Frequency:** scroll a digit or click its upper or lower half. ← → tune by the chosen step. You can also type a
  frequency (14.250 = MHz) and press Set. Each band button remembers your last frequency and mode in that browser.
- **Modes:** LSB, USB, CW, CWR, AM, FM, WFM, RTTY, DIG, and **LSB voice**. For voice through the Digirig use **DIG**
  for USB and **LSB voice** for LSB. In plain LSB/USB the FT-897 ignores the Digirig's audio. "Voice through the
  Digirig" makes the band buttons pick these for you.
- **LSB voice:** the radio stays in DIG with its dial 3 kHz below the carrier, and the Pi flips the audio. It is
  real LSB on the air. The panel shows the true LSB frequency, and the radio's own display shows 3 kHz less. It needs
  the voice client, and doesn't work with split or VFO A/B.
- **VFO A/B, Split, Lock, RIT.** RIT can be set but not read back (the 897 can't report it).
- **FM:** repeater shift, offset, CTCSS tone, Tone/TSQL, DCS/DSQL, and **Simplex** (turns shift and tones off). The
  897 can't report these either, so the panel shows what was last set over CAT, and "?" until something sets them.
  The transmit frequency is shown in amber, or red when it would be out of band or locked.

### 5.3 Meters

- **S-meter** while receiving. With an audio client connected it is off by default, because reading it puts faint
  clicks into the audio. Tick **Live S-meter** to turn it on (the voice client removes most of the clicks).
- **Power, ALC, SWR** while transmitting. The FT-897 reports them in 16 steps, not watts or a ratio. Power is
  relative to the power set on the radio.

### 5.4 Waterfall

- It shows the receive audio from 0 to 3.4 kHz, newest line at the top. The FT-897 has no wide-band output, so this
  is not a band scope.
- The **blue lines** are your passband: drag them. A **red line** is the manual notch: double-click to set it.
- Point at a signal to read its audio frequency and, in SSB/DIG, its RF frequency.
- Speed, brightness and contrast are remembered. While you transmit it shows your transmit audio with a red edge.
- Up to 6 viewers at once. It stops while the browser tab is hidden.

### 5.5 Tune in voice (USB, LSB, DIG)

Press **Tune in voice** while someone is talking. After 4 s or more it moves the dial so the voice sounds natural.
Press it again to stop, or **Undo** to go back. On a weak voice it keeps listening for up to 45 s, and after 9 s it
offers its best guess as a button. "Automatically, once after I tune" does this once after each dial change. It moves
your transmit frequency too.

It works from the voice's overtones, so it needs speech and does nothing when it's unsure. In simulation it was sure
about 83% of clips at 10 dB above the noise and 56% at 0 dB, and about 1 in 60 of those were wrong, usually by
100–250 Hz. FT8 and similar signals can fool it.

### 5.6 Scan and band scope (USB, LSB, DIG)

- **Up / Down** step 1.5 kHz through the voice segments you tick: US Technician, General, Extra; the 60 m channels;
  SSB parts of 6 m, 2 m and 70 cm; or a **Custom** range. The scan stops only for a voice. Then it tunes the voice in
  and does what **On a voice** says: stop there, go on when it goes quiet, or go on after 10 s.
- **Skip always** remembers a frequency in that browser.
- **Band scope** draws the range in 2.3 kHz pieces. Click a signal to tune to it.
- Using PTT or tuning by hand stops the scan, and that PTT press transmits nothing.
- 200 kHz takes about 48 s to scope and about 80 s to scan. Broadcast music can stop it, and a station that is silent
  as the scan passes is missed.

### 5.7 PTT

Hold the **PTT** button or the **Space bar**. Release to un-key. If the browser closes or the network drops, the Pi
un-keys within about 1 s. After any safety un-key, a PTT that is still held does nothing: press it again.

### 5.8 Repeaters and favorites

- **Repeaters tab:** search, filter by band, add, edit, mark favorites. **Import** a CSV exported from your own
  RepeaterBook account, or a CHIRP CSV. The list is stored on the Pi, so every computer sees the same one. The
  project ships with an empty list, because RepeaterBook data may not be redistributed.
- **Favorites tab:** **Save what I have now** stores the frequency, mode and repeater settings, and optionally the
  receive and transmit audio settings (never volume, mic gain or drive). One click brings it all back, from any
  computer.

### 5.9 Recording and test tone

- **Record** (button or the `r` key) saves the last 30 s and what follows to `~/FT897 Recordings`. `_raw.wav` is the
  radio's audio untouched; `_heard.wav` is after the processing.
- **Test tone 3 s:** after a confirmation, it keys the radio with 3 s of 1 kHz at the Drive level. The tone starts
  only once the voice client says it's ready. A second click, leaving the page or losing the network ends it. On
  SSB/DIG a tone is full power: turn the power down or use a dummy load. Identify, and remember that linked
  repeaters carry it everywhere.

## 6. Audio settings reference

All audio settings live in the **voice client** on your laptop. They're remembered in `~/.ft897-audio.json` and
changed from the panel's **Audio & filters** tab. Double-click a slider to put it back to its default.

### 6.1 Receive

| Setting | Range | Default | What it does / advice |
|---|---|---|---|
| Volume | −40 to +20 dB | −12 | Headset volume. Also beside the waterfall and on the `+` `-` keys |
| Mute | | off | Receive is also muted automatically while you transmit (loop protection) |
| Noise reduction | 0 (off) to 24 dB | 8 | How much noise is removed. Standard method: 8 has no artefacts, 13 is slightly watery, 20 clearly "tinkles". For weak voices more is not better: try 6–10 |
| Noise method | Standard / Neural (RNNoise) | Standard | **Standard** removes steady hiss. **Neural** is a small network trained on speech, and adds 20 ms of delay. On one 40 m recording it kept the voice about 7 dB louder at the same noise; on a very weak 20 m voice it turned the voice down too. Compare both on the same station. Mac only for now |
| Low cut / High cut | 50–1500 / 600–3400 Hz | 100 / 2800 | The passband. Also on the preset buttons (SSB, SSB mid, SSB narrow, CW 500, CW 250, FM, AM) and the blue lines in the waterfall |
| Bass / Treble | ±12 dB | 0 | Tone controls (500 Hz / 1.5 kHz shelves) |
| Levelling | Off / Slow / Fast | Off | AGC. Fast suits nets with mixed signal levels |
| Auto notch | on/off | off | Finds and removes whistles and carriers (−62 dB on a steady tone). Leave it off for CW |
| Manual notch, Notch at, width | 100–3300 Hz, 20–400 Hz | off, 1000, 100 | Double-click the waterfall to place it |
| Remove CAT ticks | on/off | on | Repairs the clicks CAT data leaks into the audio; loud speech is left alone |
| Voice squelch | on/off | off | Mutes when no voice is detected |
| FM squelch | 0 (off) to 30 | 0 (presets: 8) | Mutes by the hiss above the voice. 8 is a good start; below 6 it stays open |

**Receive presets:** SSB everyday, SSB noisy band, SSB weak signal / DX, SSB net, AM, CW wide, CW narrow, FM strong
repeater, FM AllStar standard, FM noisy signal, No processing. "Change the preset with the radio's mode" switches
presets automatically. Presets you save yourself are stored in that browser.

**Weak-signal tips:** use a wired headset; use "SSB: weak signal / DX"; keep Noise reduction moderate; replay with
Record; and set the radio's DSP and noise blanker before you leave. A quieter antenna helps more than any processing.

### 6.2 Transmit

| Setting | Range | Default | What it does / advice |
|---|---|---|---|
| Drive (ALC) | −20 to −1 dBFS | −5 | Where your peaks are limited. Set it so the ALC just moves (section 4). The administrator can cap it |
| Mic gain | ±20 dB | 0 | Extra microphone gain before processing |
| Compression | 0 to 14 dB | 8 | Raises quiet syllables. 2–4 for a natural sound, 8 for DX punch |
| Clarity | 0 to 12 dB | 6 | Presence boost around 2 kHz |
| Low cut / High cut | 80–500 / 2000–3200 Hz | 300 / 2800 | FM presets use 300–3000 to keep voice clear of the CTCSS tone |

**Transmit presets:** SSB everyday, SSB DX punch, SSB natural, FM AllStar style, FM louder. The meters **Mic now** and
**Sent** show your levels live.

### 6.3 Keys in the voice client window

`+` `-` volume · `n` noise reduction (off / 8 / 13 / 20) · `]` `[` transmit drive · `r` record.

## 7. Administration page reference

`http://<Pi address>:8080/admin`. Nothing changes until you press **Save** (**Undo** discards your changes).

| Section | Setting | Default | Notes |
|---|---|---|---|
| Transmitting | Allow transmitting | on | Off = receive only, for everyone; a transmission under way stops |
| | Only inside the bands below | on | Untick it if you're licensed outside the amateur bands (MARS, CAP) |
| | Longest transmission | 300 s | 10–600 s. Then the radio is un-keyed |
| Bands | Transmit per band, From/To MHz, reason | US edges | A locked band can still be listened to. Applies to the panel, WSJT-X and all CAT programs. It can't stop the microphone or a key plugged into the radio |
| Locked ranges | From/To MHz, reason | none | E.g. where your antenna isn't matched, or a local band-plan limit |
| High-SWR shutdown | Shutdown on | on | The meters are read about 5 times a second while transmitting |
| | Use the radio's own high-SWR warning | on | |
| | Also stop at this SWR meter step | 4 (FT-891: 7) | 1–15. A good antenna shows 0 or 1. The author's radio read step 2 at about 1.3:1 |
| | Readings in a row | 3 | 1–20; 3 is about 0.6 s |
| | Who may reset it | anyone who may use the radio | or only the administrator. The band stays locked until reset |
| Who may use the radio | Nobody may tune or transmit without taking control | off | Turn it on together with an operator password |
| | Time to answer a control request | 30 s | 10–300 |
| | If there is no answer | They get control | or They don't |
| | Release control after no contact | 120 s | 30–3600 |
| | Other names this panel may be opened by | none | Only for names with a dot, e.g. a DDNS name. Other websites can't control the radio |
| Locked settings | Transmit audio, Test tone, Repeater list | off | Only the administrator can change the ticked ones |
| | Highest drive for voice | −1 (no limit) | −20 to −1 dBFS |
| Sound levels on the Pi | Receive (Mic capture), Transmit (Speaker) | empty | 0–35 / 0–37. Empty = left alone. See section 4 |
| Passwords | Operator password, administrator password | none | Stored as scrypt hashes. Five wrong tries block that computer for 5 minutes. Forgot the admin password: delete `pi/passwords.json` on the Pi (the locks stay) |

## 8. Command-line options

### 8.1 Bridge (on the Pi): `ft897-netbridge.py`

Add them to `./install.sh`, which writes them into the service. Run `python3 ft897-netbridge.py --help` for the full
list.

| Option | Default | Meaning |
|---|---|---|
| `--radio ft897d\|ft891` | ft897d | Which radio |
| `--serial` | the Digirig's CP2102 | CAT serial device |
| `--baud` | 38400 (FT-891: found by asking) | Must equal the radio's CAT RATE menu |
| `--ptt RTS\|DTR\|CAT` | RTS (FT-891: CAT) | How to key the radio |
| `--ptt-serial` | the CAT device | Separate device for RTS/DTR keying |
| `--model` | usual | Hamlib model, e.g. 1023 = FT-897, 1022 = FT-857 |
| `--card` | first USB sound card | ALSA device |
| `--cat-port` / `--audio-port` / `--web-port` | 4533 / 4534 / 8080 | Network ports (web 0 = off) |
| `--bind` | 0.0.0.0 | Address to listen on |
| `--stall` | 1.5 s | Un-key if the laptop's transmit audio stops this long |
| `--no-audio-check` | off | Allow keying with no laptop audio link (e.g. CW keyer). Use with care |
| `--dummy` | off | Test mode: no radio, no serial port, no sound card |
| `-v` | | Log PTT, frequency changes and connections |

### 8.2 Laptop client: `client/ft897-audio.py`

| Option | Default | Meaning |
|---|---|---|
| `--server` | from `server.txt`, else asks | The Pi's address |
| `--voice` | off | Use your headset (the system's default devices) instead of BlackHole / VB-CABLE |
| `--open-panel` | off | Open the web panel in the browser |
| `--rx-audio` / `--tx-audio` | BlackHole 2ch / CABLE | Audio devices (part of a name, or `default`); `--list` lists them |
| `--rx-buffer` / `--tx-buffer` | 200/160 ms (voice 400/300) | Raise on poor Wi-Fi; lower for less delay |
| `--rx-gain`, `--nr`, `--voice-peak`, `--mic-gain` | last used | Starting volume, noise reduction, drive, mic gain |
| `--no-processing` | off | Send the microphone as it is |
| `--rec-dir` | `~/FT897 Recordings` | Where recordings go |
| `-v` | | Log buffer under- and overruns |

## 9. Safety: what the software does, and what it can't

**The bridge un-keys the radio itself:**

- when the laptop's transmit audio stops for 1.5 s (network drop, laptop asleep, client closed)
- when the program that keyed it disconnects (WSJT-X crash, network drop), or the panel's PTT heartbeat stops for
  about 1 s
- after the longest-transmission time (300 s by default)
- on high SWR, or when the dial is turned into a locked band or range (within about half a second)
- when the radio stops answering. The bridge checks the radio's own status bytes, which Hamlib can't fake: tuning and
  transmitting are refused until the radio answers again.
- after every un-key, it asks the radio whether it is really back in receive. If not, it un-keys again, locks
  transmitting, and shows the problem in red on the panel. A radio that comes back still transmitting after it
  stopped answering is un-keyed at once.

**Also:** transmit audio reaches the radio only while it is keyed by CAT. Receive audio is muted at the laptop while
transmitting, so it can't loop back. The end of each transmission isn't cut off. Requests from other websites are
refused, raw CAT bytes from programs are refused, and a held PTT doesn't re-key after a safety un-key.

**What it can't do:**

- It can't switch the radio off, and it can't stop the microphone, a key, or another program plugged into the radio
  itself.
- If the radio itself hangs while transmitting, no command reaches it. **Have a remote power cut.**
- The FT-897's meters are coarse (16 steps). The SWR shutdown is a backup, not a substitute for a matched antenna.
- The radio's own repeater shift (ARS) and front-panel changes are not seen by the software.

**Recommended:** a remote-switchable DC supply with the internal batteries removed; the radio's own power set
sensibly; first sessions at low power, watched from the shack; a timeout on the radio where it has one (FT-891 TX
TOT, menu 05-14).

## 10. Troubleshooting

| Symptom | What to check |
|---|---|
| Panel says the radio is not answering | Is the radio on? Is CAT RATE (019) the same as `--baud`? Is the CAT cable seated? `journalctl -u ft897-netbridge -n 50`. If the radio's screen is dark or it is warm and unresponsive, see [Known issues](#12-known-issues-and-limitations) |
| WSJT-X "rig control error" | Same as above. Network Server is `<Pi address>:4533`, not a `.local` name over a VPN |
| No waterfall, or a waterfall but no sound | Is an audio client connected (its window says "connected")? Is your headset the system's sound output? Mac: the client warns if output is left on BlackHole. Check the Pi's Receive level (12) |
| Radio doesn't key | `--ptt RTS` vs `CAT` for your cable; the log shows "radio KEYED". Is transmitting allowed, and the band unlocked (admin)? Did you take control? Is an audio client connected (needed unless `--no-audio-check`)? |
| PTT refused, "out of band" | The transmit frequency (shown under the dial) is outside an unlocked band. Check the repeater shift and offset, or press Simplex |
| Voice heard but nobody hears you | DIG or LSB voice, not plain USB/LSB. The Tone is on and correct for the repeater. Drive isn't too low (watch the ALC and Sent meters) |
| Audio drops out, "network stall", TX underruns | Wi-Fi. Use 5 GHz, keep the laptop near the access point, close Wi-Fi scanners and the Wi-Fi settings pane, and on a Mac try AirDrop off. Put the Pi on Ethernet if you can. Raise `--rx-buffer` |
| Faint crackle in receive | CAT data leaking into the audio. Keep "Remove CAT ticks" on, and leave Live S-meter off when you don't need it |
| Noise reduction sounds watery or "tinkles" | Lower it to 6–8, or compare the Neural method |
| Weak station unreadable | See the weak-signal tips in 6.1 |
| Bluetooth headset silent or robotic | Wait for the 2 s mic warm-up. Both RX and TX must show the same rate (24000 Hz for AirPods). Wired is better |
| "radio in use by …" | Another laptop's audio client is connected. Close it there; an idle one is replaced after 5 s |
| Admin password forgotten | Delete `pi/passwords.json` on the Pi, then open /admin to set a new one |
| Digirig re-plugged and levels far too loud | The bridge restores the admin page's levels automatically; check that they are filled in |
| Bridge won't start | `journalctl -u ft897-netbridge -n 50`. Is the radio's serial port taken by another program? Never open the CAT port with another program while the bridge runs (RTS = PTT) |

**Restarting the bridge** drops audio for a few seconds, signs everyone out and clears Take control. Check that
nothing is transmitting first: `sudo systemctl restart ft897-netbridge`.

## 11. Testing

### 11.1 What has been tested

| Area | Status |
|---|---|
| FT-897D CAT, PTT on RTS, meters, FM repeater shift and tone | On air, one radio |
| Voice on FM (repeaters) and SSB (DIG and LSB voice) | On air; LSB voice confirmed on frequency on several web SDRs |
| WSJT-X over the network | On air |
| Tune in voice, scan | On air (a few tests) plus simulation |
| High-SWR shutdown, dial-turn un-key, split lock, DCS | Simulation only |
| FT-891 | Simulation only (a pretend radio built from Yaesu's CAT book) |
| Windows voice, Linux client | Not tested |
| Neural noise reduction | Recordings and one evening on air; Mac only |

### 11.2 Automated tests

On the Pi (or any Linux machine with Hamlib):

```bash
pi/tests/run_all.sh        # bridge in --dummy mode: stuck transmitter, LSB voice, dead radio, repeater state, access (254 checks)
pi/tests/run_ft891.sh      # the whole bridge against a pretend FT-891 (76 checks)
```

Both run in `/tmp` on different ports and never touch a running bridge or the radio.

### 11.3 Testing safely on your own station

1. Start with the bridge in `--dummy` mode (stop the service first) to learn the panel without a radio.
2. Then use the real radio into a **dummy load** at low power, at the radio, with the panel on a laptop beside it.
3. Try each safety: close the browser while keyed, switch off Wi-Fi while keyed, lock a band and key on it. Each
   should un-key within about 1 s.
4. Only then operate remotely, at first with someone near the radio.

### 11.4 Reporting problems

Open an issue on GitHub with: radio model, Hamlib version (`rigctld --version`), what you did, what happened, and the
log (`journalctl -u ft897-netbridge --since "10 min ago" -o cat`). **Remove your IP addresses, callsigns of others, and
passwords before posting.**

## 12. Known issues and limitations

- **One radio lock-up (2026-11-24).** During remote use the FT-897D stopped answering CAT and its audio went silent,
  right after a transmission ended. At the radio, the screen was dark and the radio was very warm. It recovered only
  after the DC and the internal batteries were removed, and has worked normally since. **The cause isn't proven.** The
  leading suspect is CAT polling while transmitting (then about 10 reads a second); RF getting into the radio, a supply
  dip or a firmware fault are also possible. It also can't be ruled out that the radio stayed keyed for part of that
  time. Since then:
  - polling while transmitting is about 5 meter reads a second, and while receiving with audio connected, one
    frequency and mode read every 10 s
  - the bridge checks the radio's own status after every un-key and alarms if it doesn't return to receive
  - a radio that stops answering is detected, and transmitting is refused
  - the remote power cut and battery removal are now recommended above

  If it happens to you, please report it with the log (11.4).
- The FT-897D can't report RIT, repeater shift, offset, tone or DCS, and has no CAT command for AF/RF gain, squelch,
  noise blanker, DSP, AGC, IPO/ATT, memories or menus.
- The FT-897D only takes rear-jack audio in DIG/PKT modes, which is why DIG and LSB voice are used for voice.
- Reading the meters or S-meter leaks faint clicks into the receive audio through the Digirig cable. The client removes
  most of them.
- Levels, squelch thresholds and drive defaults come from one radio; calibrate yours (section 4).
- The band edges, scan segments and band plan are US only.
- Neural noise reduction is Mac only. Windows voice and the FT-891 are untested.
- Only Hamlib 4.5.5 has been tested. Other versions may answer the raw meter reads differently.
- The web panel isn't encrypted. Use it only on your own network or over a VPN.

## 13. Yaesu CAT documentation

Yes, Yaesu publishes the CAT protocol for both radios:

- **FT-897D:** the *CAT System* chapter of the FT-897D Operating Manual (around page 63) documents the 5-byte binary
  commands: four parameter bytes then the command byte, 8 data bits, no parity, 2 stop bits. It covers frequency, mode,
  PTT, split, lock, clarifier, repeater shift and offset, CTCSS/DCS, and the receive/transmit status reads. Some
  commands that Hamlib and this bridge use, such as the meter read (0xBD) and the EEPROM read, are not in the manual;
  they were worked out by the amateur community. This software only ever **reads** the EEPROM, because writing it has
  damaged radios.
- **FT-891:** the *FT-891 CAT Operation Reference Book*, a separate document of ASCII commands ending in `;`
  (e.g. `FA014250000;`). Available from Yaesu and mirrored online.

## 14. Files, updating and removing

| Where | What |
|---|---|
| `pi/` on the Pi | the bridge, panel and admin page, plus your `settings.json` (locks, levels), `passwords.json` (hashes) and `repeaters.json`. These three are never part of git |
| Laptop `~/.ft897-audio.json` | your voice audio settings |
| Laptop `server.txt` (project folder) | the Pi's address |
| Laptop `~/FT897 Recordings` | recordings |
| Browser storage | band memories, your own presets, waterfall settings, skipped scan frequencies |

- **Update:** `git pull` in the project folder on the Pi, run `pi/install.sh` again, and restart the laptop client.
  Your settings, passwords and repeater list are kept.
- **Remove:** `pi/install.sh --remove` on the Pi, then delete the folder.

---

yaesu-netradio is GPL-3.0, by W5PL. It's not affiliated with Yaesu or Digirig. See the README for credits.
