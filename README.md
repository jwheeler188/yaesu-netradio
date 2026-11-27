# yaesu-netradio: W5PL FT-897D / FT-891 network radio

Runs a Yaesu FT-897D (or FT-891, see the end) with a Digirig from a Raspberry Pi, so WSJT-X / VARA / Pat on a Mac
**or** Windows laptop can use it from anywhere in the house, or from away over a VPN, and anyone can operate voice
from a web page with a headset. In the examples below **192.168.1.50** stands for your Pi's address: use its number
rather than `raspberrypi.local` if you connect over a VPN, where `.local` names don't work.

**New here? Read the [user manual](docs/USER-MANUAL.md)**: setup, every setting, troubleshooting, testing and known issues.
This is **beta** software, and remote operation is at your own risk.

Tested on: Ubuntu 24.04 on a Raspberry Pi 4, Hamlib 4.5.5, one FT-897D with a Digirig, macOS laptops. Windows voice
and the FT-891 are not yet tested on real hardware. Reports welcome in the GitHub issues.

```
FT-897D ──Digirig──USB──► Pi: ft897-netbridge (systemd)
                              ├─ CAT/PTT   TCP 4533  (Hamlib rigctld protocol, FT-897D model 1043, PTT on RTS)
                              └─ audio     TCP 4534  (48 kHz 16-bit mono, ~0.8 Mbit/s each way)
Laptop: WSJT-X ──Hamlib NET rigctl──► <Pi>:4533
        WSJT-X ◄──BlackHole / VB-CABLE──► ft897-audio.py ──► <Pi>:4534
```

## 1. Radio and Pi (once)

1. Plug the Digirig's USB cable into the Pi. Get this project onto the Pi:
   `git clone https://github.com/jwheeler188/yaesu-netradio` (or copy the folder over).
2. FT-897D menu **019 CAT RATE = 38400** (or use the bridge's `--baud` to match yours). Power-cycle the radio
   after changing it.
3. Install the bridge on the Pi (it asks for your sudo password):
   ```bash
   ssh -t pi@192.168.1.50 'cd ~/yaesu-netradio/pi && ./install.sh'
   ```
   If your Digirig cable keys the radio through CAT instead of RTS, use `./install.sh --ptt CAT`.
4. Check the log. You should see "radio answering: 14.0740 MHz" (or your frequency):
   ```bash
   ssh pi@192.168.1.50 'journalctl -u ft897-netbridge -n 30 --no-pager -o cat'
   ```
5. Audio levels: run `alsamixer -c Device` on the Pi (F6 to pick the USB card) and set Mic (RX level) and Speaker
   (TX level), or set them on the administration page. Then save them with `sudo alsactl store`. The bridge turns
   the CM108's capture AGC off when it starts.
   Measured on W5PL's radio (2026-11-22): **Mic capture 12** (0 dB) gives about -30 dBFS receive audio with no
   clipping. **Speaker 2** (-35 dB): ALC starts at about -10 dBFS of app audio, and there is no ALC at -12 dBFS,
   so set WSJT-X's **Pwr slider to about -12 dB**.

## 2. Mac

1. Install [Homebrew](https://brew.sh) and BlackHole 2ch (`brew install --cask blackhole-2ch`, then restart), then run
   `macOS/install.sh` once.
2. Double-click **macOS/Start FT-897 Audio.command**, or run `macOS/ft897.sh`. The first time it asks for the Pi's
   address and saves it in `server.txt`. Leave it running.
3. WSJT-X › Settings › Radio: Rig **Hamlib NET rigctl**, Network Server **192.168.1.50:4533**,
   PTT Method **CAT**, Mode **Data/Pkt** or USB. Audio: Input and Output **BlackHole 2ch**.
4. xara/VARA: `--ptt rigctld:192.168.1.50:4533`. Pat: rig address `192.168.1.50:4533`.

Only one program at a time can use BlackHole 2ch.

## 3. Windows

1. Install Python 3 **x64** from python.org and tick "Add python.exe to PATH". On a Windows PC with an ARM chip the
   x64 version is still needed, because PyAudio has no ARM64 package. Install VB-Audio Virtual Cable (vb-audio.com/Cable).
2. Copy this folder to the PC. Double-click **windows/Start FT-897 Audio.bat**. The first time, it
   installs pyaudio and asks for the Pi's address.
3. WSJT-X: Rig **Hamlib NET rigctl**, Network Server **192.168.1.50:4533**, PTT **CAT**,
   Input **CABLE Output**, Output **CABLE Input**.

## Web control panel and voice

Open **http://192.168.1.50:8080** (your Pi's address) in any browser: Mac, Windows, phone.

- **Frequency:** scroll a digit or click its upper/lower half to tune it. The ← → keys tune by the selected step.
  You can also type a frequency (14.250 = MHz) or use the band buttons. Each band remembers your last frequency
  and mode in this browser.
- **Other controls:** mode, VFO A/B, Split, dial Lock, and RIT. RIT can be set but not read back: the 897 can't
  report it. In FM you also get repeater shift, offset, CTCSS tone, and Tone/TSQL.
- **Meters:** the S-meter shows while receiving (click it, or tick "Live S-meter" in the Options tab, while an audio client is connected). Power, ALC and SWR show while transmitting, from the panel or
  from WSJT-X. The 897 reports them in 16 steps (SWR as meter steps, not a ratio). The bridge reads them with CAT
  command 0xBD, because Hamlib 4.5.5 returns the power as "ALC" and only a high-SWR flag as "SWR".
- **Waterfall:** the audio passband (0-3.4 kHz, not the whole band: the 897 has no wide-band output), newest at the
  top, with the spectrum above it. Speed, brightness and contrast are remembered. Point at a signal to read its audio
  frequency and, in USB / LSB / DIG, its RF frequency (DIG is taken as USER-U). While transmitting it shows the
  transmit audio with a red edge. It is listen-only (`GET /api/audio`, 8 kHz, about 130 kbit/s per viewer, at most 6),
  works with the voice or FT8 client connected, and stops while the tab is hidden.
- **Tune in voice** (USB, LSB, DIG): listens for 4 s or more and moves the dial so that the voice sounds right; press it again to stop, Undo to go
  back. It works from the overtones of the voice, so it needs someone talking, and does nothing when it isn't sure. "Automatically, once after I
  tune" does it by itself after you turn the dial, once, and then stays put. It moves the transmit frequency too. On a weak voice it keeps listening (up to 45 s) and after 9 s
  offers its best guess as a button. First on-air measurement (2026-11-24, voice about as strong as the noise): +209 Hz found, +210 Hz was right. Simulated,
  share of clips it was sure about after 8 s / 24 s: 83 / 92 % at 10 dB over the noise, 67 / 83 % at 3 dB, 56 / 72 % at 0 dB, 25 / 47 % at -3 dB; about 1 in 60
  of those wrong, usually by one step of the voice's pitch (100-250 Hz). FT8 and the like can fool it when you ask for it there. Not for FM and AM.
- **Scan** (USB, LSB, DIG): **Up** / **Down** steps 1.5 kHz through the voice segments you pick (US: Technician, General,
  Extra; 60 m channels; SSB parts of 6 m, 2 m and 70 cm; or Custom) and listens more closely
  where something is there. It stops only for a voice: overtones of a moving pitch. It then tunes it in, and stops, goes on when
  it goes quiet, or goes on after 10 s. **Skip always** remembers a frequency in that browser. **Band scope** draws the range in 2.3 kHz pieces; click a
  signal to tune there. The radio's sound follows the scan. PTT or tuning by hand stops it, and that PTT press transmits nothing. 200 kHz at Normal speed: scope 48 s
  (measured on the radio), scan about 80 s plus 1.5 s per other signal. Simulated: 34 of 40 voices found (3-20 dB over noise), 0 false stops in 4 sweeps past birdies,
  CW, FT8, RTTY, PSK and power-line noise. Broadcast music can stop it. A station that is silent as the scan passes is missed until the next pass.
- **PTT:** hold the button or the Space bar. If the browser closes or the Wi-Fi drops while you're transmitting,
  the Pi un-keys the radio within about 1 s.

**Voice:**
1. Pick your headset as the computer's sound output and input. On the Mac that's System Settings › Sound.
2. Double-click **macOS/Start FT-897 Voice.command** or **windows/Start FT-897 Voice.bat**. It starts the audio
   client with that headset and opens the panel. After a one-time setup (`macOS/install-link.sh`, run by install.sh, or
   `windows/install-link.bat`), the panel's **Start voice client** / **Stop voice client** buttons do the same from the
   page. Closing the browser doesn't stop the voice client.
3. Use **DIG** for USB and **LSB voice** for LSB (menu 038 DIG MODE = USER-U). The 897 only takes audio from the rear DATA jack in
   DIG and PKT modes, not in plain USB/LSB. **LSB voice** is real LSB on the air: the radio stays in DIG with its dial 3 kHz below the
   carrier, and the Pi turns the audio spectrum over (f becomes 3000 - f) both ways, so waterfall, voice tuning and scanning work as on LSB.
   The panel shows the true LSB frequency, the radio 3 kHz less. Needs the voice client; a CAT program that tunes, a data audio client or another mode
   ends it; not with split or VFO A/B. Transmit locks check the carrier and the 2.8 kHz below it. Measured: tones exact, images 60 dB down, 99.96 % of
   the signal inside the LSB channel, same drive as USB within 0.5 dB (simulated radio filter).
4. **Audio & filters tab** (settings live in the voice client and are remembered there): volume, mute, noise reduction
   (0-24 dB), passband (also by dragging its edges in the waterfall) with sharp edges (47 dB down 100 Hz outside) and a Shift control, auto notch, two manual notches (double-click the
   waterfall), levelling, bass/treble, voice squelch; for transmit: drive, mic gain, compression, clarity, low/high cut.
   Measured: noise reduction 8 dB is free of artefacts, 13 dB slightly and 20 dB clearly "tinkles"; auto notch takes
   62 dB off a tone. There is no noise blanker here: the radio's filter has smeared the pulses by then, so use its NB.
   **Noise method**: *Standard* (above) or *Neural* — [RNNoise](https://github.com/xiph/rnnoise) (Xiph.Org, BSD licence,
   `client/rnnoise/`, rebuild with `build-mac.sh`), a small network trained on speech; the Noise reduction slider still
   sets how much is removed. Adds 20 ms of delay. On a 7 MHz SSB recording it kept the voice about 7 dB louder than
   Standard at the same noise; on a very weak 14 MHz one it turned the voice down too, so compare both. Mac only for now
   (Windows needs an `rnnoise.dll` next to it).
   **Presets** (receive and transmit, also beside the waterfall) set the filters, noise reduction, levelling and
   compression, never volume, mute, mic gain or drive. "Change the preset with the radio's mode" does it automatically.
   Your own presets are saved in that browser. The FM ones follow AllStar's documented filters (receive 250-3300 Hz or
   300-3000 Hz, transmit 300-3000 Hz so voice stays clear of the CTCSS tone); the 897's data jack already de-emphasises.
   **FM squelch** (0 = off, 8 is a good start; below 6 it stays open on this radio): mutes by the hiss above the voice, 3.4-6 kHz: -49 dBFS with no signal and
   -79 dBFS on a full-quieting repeater at this setup's Mic level 12. Needed because the 897's data jack ignores the
   radio's own squelch. It closes 0.1-0.2 s after the signal goes. The radio has no CAT command for its squelch knob.
   **Test tone 3 s**: after a confirmation, keys the radio with 0.7 s of silence and 3 s of 1 kHz at the Drive level instead
   of the mic (check it on your repeater's web audio monitor, if it has one). Keyed only once the voice client
   reports the tone ready; a second click, leaving the page or losing the Wi-Fi ends it. On SSB / DIG it is full power.
5. Keys in the client window: **+ -** volume, **n** noise reduction, **] [** transmit drive, **r** record. Talk and set
   the drive so the radio's ALC just moves on peaks (SSB).
6. **Record** (key or button) saves the last 30 s and what follows in `~/FT897 Recordings`: `..._raw.wav` is the radio's
   audio untouched, `..._heard.wav` is after the processing.
7. Bluetooth headsets (AirPods): the client turns the mic on for 2 s first, because that switches them to headset mode
   (mono 24 kHz). The "connected" line must show the same 24000 Hz for both RX and TX.
   On a Mac the client follows the sound settings: choose another output or input and it switches over by itself.
8. FM works through the Digirig too (2 m repeater contact, 2026-11-23).
9. Close the FT8 audio client first: voice and FT8 use different audio devices, so only one runs at a time.

**CW keyer** (mode button): send CW from the keyboard. The radio stays in DIG with its dial a pitch (700 Hz) below the carrier
and the Pi plays a keyed tone with 5 ms raised-cosine edges, which on the air is a single keyed carrier: CW. Space (or the PTT button)
is a straight key; ← → work as a bug (← dits, → dah) or iambic paddles. The panel times the keying on its own clock and the Pi replays it
0.25 s later, so network jitter never changes a dit; sidetone plays in the browser at once. Type-ahead text with prosigns and
editable buttons (CQ, RST, 73...), Farnsworth spacing, 5-50 WPM, hang time. The Pi decodes what it hears at the pitch and shows it,
following a station's speed and up to ~150 Hz of offset. Sending needs no voice client. Tested by decoding what the Pi would send.

**Winlink on the Pi** (optional, Winlink tab): xara (VARA HF-compatible modem) and Pat run on the Pi; open Pat's mailbox in the
browser, connect to a gateway, Pat tunes the radio. xara reaches the radio through a loopback sound card and the bridge's own audio
and CAT ports, so every lock and safety check applies. Setup: manual section 3.6.

**Repeaters and favorites:** one click sets frequency, mode, shift, offset and CTCSS or DCS. The list is kept on the Pi
(`pi/repeaters.json`), so every panel shows the same one. Import the CSV you export from your own RepeaterBook account
(personal use; digital-only, off-air and out-of-band entries are skipped) or a CHIRP CSV, or use Add current. Data courtesy
of RepeaterBook.com. Direct download needs an app token that RepeaterBook approves.

**Favorites** hold any mode: **Save what I have now** keeps the frequency, mode, repeater settings and, if you like, the receive and transmit audio
settings (not volume, mic gain or drive). One click brings it all back, from any computer. **Edit** changes or deletes them. Audio needs the voice client.

**Repeater settings on the display** (FM): shift (+5), tone (TONE / TSQL / DCS / DSQL), and the frequency you will transmit on are shown above and below the
frequency, in amber; in red when that would be out of band or locked. **Simplex** switches shift and tones off. The FT-897 can't report these, so the bridge shows what was last set
over CAT (panel, favorite or program) and "?" until something sets them; a change on the radio's own buttons, or its automatic repeater shift, is not seen.
Transmit locks use the shifted frequency. An option clears them when you tune more than 50 kHz away from a repeater.

**Take control** (top right) gives your computer the radio:
- If nobody else is using it, you get it at once. If someone is, they see a question above their waterfall and can hand it over
  or keep it. With no answer in 30 s you get it (or not: administration page), but never in the middle of a transmission.
- The administrator can take over at once, which ends any transmission.
- Every other computer's WSJT-X and audio connections are then closed, and their reconnects are refused until you
  press **Release**. If your computer isn't heard from for 2 minutes, control is released automatically.
- With an operator password set, taking control asks for it. Computers behind one address (NAT) count as one computer.

Anyone on your network or VPN can open the panel, the same as the CAT port. Don't forward these ports to the internet: use a VPN.

## Administration page

**http://192.168.1.50:8080/admin** (link at the top right of the panel). The first visit sets the administrator password. Forgot it:
delete `pi/passwords.json` on the Pi; the locks stay. Passwords are stored as scrypt hashes, but the page itself is not encrypted on your
network, so use one you use nowhere else. Five wrong passwords from a computer block it for 5 minutes.

- **Bands and ranges:** lock transmitting per band, change band edges, and lock parts of a band, with a reason shown to the user. Listening
  always works. It applies to the panel, WSJT-X and every CAT program; with split it checks the transmit VFO. Turning the dial into a
  locked band while transmitting un-keys the radio within about half a second. It can't stop the microphone or key plugged into the radio.
- **High-SWR shutdown** (on by default): while transmitting, also without a panel open, the meters are read about 5 times a second. The radio's own
  high-SWR warning, or its SWR meter at the set step (4 of 15) for 3 readings, un-keys it and locks that band until someone presses Reset.
  The FT-897 gives meter steps, not a ratio. It is a backup: tune the antenna first.
- **Who may use the radio**, longest transmission, highest voice drive, and which settings only the administrator may change.

Other websites can't control the radio: requests must come from the panel's own pages, opened by an address or a local name (other names can be
allowed on the administration page). Raw CAT bytes from CAT programs are refused.

## One laptop at a time

The Pi accepts one audio client. If one laptop is connected, another laptop's client reports "radio in use by …" and
keeps retrying every 10 s. Close the client on the other laptop to switch over. A client that has gone silent for
5 s (a sleeping laptop, for example) is replaced automatically.

## Safety (the bridge un-keys the radio itself)

- If the laptop's audio stops for 1.5 s while the radio is transmitting (Wi-Fi drop, laptop asleep, client closed).
- If the app that keyed the radio disconnects (WSJT-X crash, Wi-Fi drop).
- If the radio has been keyed for 300 s (administration page).
- On high SWR, or on a locked band or range (administration page).
- If the radio does not answer (its raw status byte, which Hamlib can't fake): no transmitting or tuning.
- After every un-key the radio itself is asked whether it is back in receive. If not, it is un-keyed again, transmitting is locked and the panel says so in red.
- The Pi's sound levels (administration page) are set at every start and whenever the Digirig is plugged back in.
- For unattended use give the radio's supply a remote switch, and leave the internal batteries out: with them in, it stays on.
- After any of these, a PTT button that is still held does not key it again: press it anew.
- Transmit audio only reaches the radio while it is keyed by CAT, and receive audio is muted at the laptop during
  transmit, so received audio can't loop back into the transmitter.
- On PTT off, the bridge waits until the app's last audio has reached the radio, so the end of a transmission
  isn't cut off.

## Troubleshooting

| Symptom | Check |
|---|---|
| WSJT-X "rig control error" | `journalctl -u ft897-netbridge -f`: is the radio answering? Is CAT RATE 019 = 38400? |
| No waterfall | Is the audio client connected (its window says "connected")? Is WSJT-X input BlackHole / CABLE Output? alsamixer Mic level |
| Radio doesn't key | `--ptt CAT` vs RTS; the log shows "radio KEYED" when WSJT-X keys |
| Voice audio drops out, "network stall" in the client window, or TX underruns in the Pi's log | The laptop's Wi-Fi: use 5 GHz, and on a Mac try AirDrop off (its radio shares the Wi-Fi chip). The client and the Pi raise their buffers by themselves |
| Faint crackle in receive audio with the panel open | CAT data leaking into the audio in the Digirig cable. The voice client filters above 2.8 kHz, and while an audio client is connected the bridge reads only frequency and mode, every 10 s. The panel's S-meter is then off; its "Live S-meter" box turns it on (2 reads a second). "Remove CAT ticks" (on by default) in the voice client finds each tick above 8 kHz and rebuilds that millisecond wherever the audio is quiet; loud speech is left alone |
| Audio gaps on a weak Wi-Fi | Start the client with `--rx-buffer 300` |
| Test without the radio | On the Pi: `python3 pi/ft897-netbridge.py --dummy -v` (stop the service first). It sends a 1 kHz test tone |

Service: `ft897-netbridge`. Remove: `pi/install.sh --remove`. Update: `git pull` in the project folder, then run
`pi/install.sh` again. Settings, passwords and the repeater list are kept on the Pi in `pi/` and are not part of git.

## FT-891 (since bridge 2026.11.25, not yet tried on a real one)

`--radio ft891` (installer: `./install.sh --radio ft891`). What differs between radios is in `pi/ft897_radio.py`; the guide for testers is
`START-HERE-FT-891.md`. Tests: `pi/tests/run_ft891.sh` runs the
whole bridge and the real rigctld against `sim_ft891.py`, a pretend FT-891 made from Yaesu's CAT book (76 checks, Hamlib 4.5.5; the driver also with 4.7.2).
The bridge speaks CAT text to the 891 itself (raw command through rigctld), because Hamlib 4.5.5 can't do RIT, the repeater offset or split off on it and
copies VFO A to B at every mode change. PTT is CAT by default, so the radio's TX TOT menu is the backstop. Also for both radios: a radio that comes back
still transmitting after it stopped answering is un-keyed at once.

## Licence and credits

GPL-3.0 (see `LICENSE`), by W5PL. Uses [Hamlib](https://hamlib.github.io) (`rigctld`, run as a separate program),
[PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) and [pyserial](https://github.com/pyserial/pyserial), and
includes [RNNoise](https://github.com/xiph/rnnoise) 0.2 (BSD licence, `client/rnnoise/COPYING`). Repeater data, if you
import it, comes from your own RepeaterBook.com export and stays on your Pi. Not affiliated with Yaesu or Digirig.

