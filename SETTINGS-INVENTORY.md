# Control and settings inventory

Made 2026-11-24 from the code (bridge 2026.11.24, client 2026.11.24c, panel), the live Pi, and what Hamlib 4.5.5
says it can do with the FT-897D. Done since (2026-11-24b): #11, #14 and #49 are on the administration page, plus high-SWR shutdown; release item 1
(other websites, passwords) except a PIN for every transmission; release item 2 (firewall).

**Level** = who needs it: **B** basic (everyday), **A** advanced (hidden until asked for), **S** setup (once).
**Work**: small = panel only, or a few lines; medium = bridge or client work; large = a project of its own.

## 1. Already in the panel

| Group | Controls | Level |
|---|---|---|
| Tuning | digit tuning, step (11 sizes), typed frequency, 13 band buttons with memory, 9 modes, VFO A/B, split | B |
| Tuning | dial lock, RIT (set only), "Voice through the Digirig" | A |
| FM | shift, offset, CTCSS, TSQL, DCS, DSQL | B in FM, else hidden |
| Meters | S (Live S-meter box), power, ALC, SWR | B |
| Transmit | PTT (hold), test tone | B / A |
| Receive audio | volume, mute, preset, noise reduction, FM squelch | B |
| Receive audio | auto preset by mode, low/high cut, passband buttons, bass, treble, levelling, auto notch, manual notch + width, voice squelch, CAT tick remover | A |
| Transmit audio | preset, mic gain | B |
| Transmit audio | drive, compression, clarity, low/high cut, mic and sent meters | A |
| Presets | 11 receive, 5 transmit, save your own, delete | A |
| Recording | record button | B |
| Waterfall | show/hide, speed, brightness, contrast, draggable passband | B / A |
| Repeaters | search, band filter, add, edit, favorite, import CSV, clear imported | B |
| Sharing | take control, release, connected list, this computer's name | B |

Volume and the receive preset appear twice (waterfall header and Audio tab). Test tone, record, reset and the preset
buttons all sit under Transmit although only the test tone belongs there.

## 2. Exists, but not in the panel

### Radio connection (bridge command line)
| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 1 | CAT baud rate (must equal menu 019) | `--baud` | S | medium, with an "auto-detect" that tries 4800 / 9600 / 38400 |
| 2 | Serial port | `--serial`, else first CP210x | S | medium: pick-list |
| 3 | Sound card | `--card`, else first USB card | S | medium: pick-list |
| 4 | PTT method RTS / DTR / CAT | `--ptt` | S | medium |
| 5 | Radio model (897D / 897) | `--model` | S | medium |
| 6 | Test mode without a radio | `--dummy` | A | small |

There is no settings file on the Pi. These need one, plus a restart of only rigctld or the audio processes, which the
bridge can do without sudo.

### Pi sound levels (alsamixer over ssh)
| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 7 | Receive level (Mic capture, 0-35) | alsamixer; 12 on W5PL's radio | S | medium |
| 8 | Transmit level (Speaker, 0-37) | alsamixer; 2 on W5PL's radio | S | medium |
| 9 | Sound card AGC | forced off at start | A | small |
| 10 | Input level meter with a clip warning | only in logs and test tools | S | small |

The service user is in the `audio` group, so the bridge can set 7-9 itself and put them back at every start
(`alsactl store` needs root; this avoids it). FM squelch thresholds (-46 dBFS) were measured at Mic level 12, so any
other level needs a squelch calibration (#42).

### Safety
| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 11 | Longest transmission | `--tx-timeout` 300 s | A | small; keep a ceiling, never "off" in the panel |
| 12 | Un-key when audio stops | `--stall` 1.5 s | A | small; limits 0.5-3 s |
| 13 | Allow keying without an audio client | `--no-audio-check` | A | small, with a warning |
| 14 | Control released after no contact | `--owner-timeout` 120 s | A | small |
| 15 | Web PTT heartbeat | fixed, about 1 s | - | leave fixed |

### Network
| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 16 | CAT / audio / web ports | command line | S | medium; the client's recorder assumes 8080 |
| 17 | Which networks may connect | `--bind`, all | S | medium |
| 18 | Waterfall viewers allowed | fixed at 6 | A | small |

### Laptop client (command line, Terminal window, or code)
| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 19 | Pi address | `--server`, else `server.txt`, else asked once and saved | S | medium: find the Pi automatically |
| 20 | Headset / speaker / microphone choice | system default only with `--voice` | B | medium: list in the panel, client applies it |
| 21 | Voice or digital mode | two launchers, one at a time | B | medium: one app with a switch |
| 22 | Receive buffer, transmit buffer | `--rx-buffer` / `--tx-buffer` | A | small ("Low delay / Normal / Poor Wi-Fi") |
| 23 | All processing off | `--no-processing` | A | small |
| 24 | Digital-mode receive volume | `--rx-gain` at start only | A | small |
| 25 | Recording folder, 30 s look-back, 10 min limit, raw/heard | `--rec-dir`, rest fixed | A | small |
| 26 | Virtual cable names | `--rx-audio` / `--tx-audio` | S | with #20 |
| 27 | Auto-switch when the sound device changes | Mac only, always on | A | Windows: medium |
| 28 | Headset settle time (2 s) | fixed | A | small |

The digital-mode (WSJT-X) client can't be adjusted from the panel at all: the panel only sends settings to a voice client.

### Stored in the wrong place for other users
| # | Item | Now | Change |
|---|---|---|---|
| 29 | Your own audio presets, band memories, computer name, waterfall settings | that browser only | presets on the Pi; the rest can stay |
| 30 | Voice settings | `~/.ft897-audio.json` on that laptop | keep (they depend on the headset); add export/import |

## 3. Behavior that is fixed in the code and worth a setting

| # | Setting | Now | Level | Work |
|---|---|---|---|---|
| 31 | Region / band plan | US band edges, offsets and default frequencies | S | medium |
| 32 | Which band and mode buttons show | all 13 + 9 (WFM, RTTY, CWR rarely used) | A | small |
| 33 | PTT style: hold or click on/off | hold only | B | small; latch needs a visible timer |
| 34 | PTT key | Space | A | small |
| 35 | Mute while transmitting | always | - | leave (loop protection) |
| 36 | CAT polling with audio running | frequency + mode every 10 s | A | small, 5 / 10 / 30 s |
| 37 | Default repeater offset per band | typed by hand, starts at 0.600 | A | small: 0.6 on 2 m, 5.0 on 70 cm, by region |
| 38 | Favorites in any mode | always set FM | B | medium: HF nets, FT8 frequencies, AM |
| 39 | Separate transmit and receive tones | only the uplink tone is kept; 3 rows of the RepeaterBook file differ (440.025, 147.100, 444.300) | A | medium: raw CAT, must be tested on the radio |
| 40 | Test tone length and frequency | 3 s, 1 kHz | A | small |
| 41 | Level-check link (parrot / monitor page) | none in the panel | A | small: user enters their own link |
| 42 | FM squelch calibration | fixed -46 dBFS reference | S | medium: "listen to an empty channel" button |
| 43 | Receive level calibration | by hand | S | medium: guided, with #7 and #10 |
| 44 | Transmit drive calibration | by hand from the ALC meter | S | medium: guided; the user keys, the panel reads ALC |
| 45 | Theme, text size, compact layout | one look | A | small |
| 46 | Waterfall colours, frequency range, peak hold | one | A | small |
| 47 | Callsign / station name shown | hard-coded "W5PL" in logs and files | S | small |
| 48 | Station ID reminder (10 min) | none | A | small |
| 49 | Transmit only inside chosen band limits | none: only the radio's own limits | A | medium |
| 50 | Scan favorites | none | later | medium; each step is a CAT write and a possible tick |

## 4. Status and upkeep (non-technical users need these most)

| # | Item | Now | Work |
|---|---|---|---|
| 51 | Plain-language problem list: radio off, wrong baud, no Digirig, card missing, clipping, wrong sound device | partly, in logs | medium |
| 52 | Connection quality: delay, stalls, TX underruns, Wi-Fi signal, Pi temperature | stalls in part; rest in logs | medium |
| 53 | View / download the log | ssh | small |
| 54 | Restart the bridge | ssh | small (it can exit and systemd restarts it) |
| 55 | Back up / restore everything (settings, presets, repeaters) | none | medium |
| 56 | Version shown, update available, update | none | medium; an update from the panel needs care with rights |
| 57 | Export the repeater list | none | small |
| 58 | Factory reset per section | audio only | small |
| 59 | Help next to each control | README only | medium |

## 5. Can't be in the panel (the radio has no command for it)

AF and RF gain, squelch knob, mic gain, RF power, noise blanker, DNR, DSP filters, AGC, IPO / ATT, keyer, memory channels,
scan, menus (including CAT RATE, DIG mode, DIG gain), and reading back RIT, shift, offset, tone or DCS. Replace with a
**radio checklist page**: what to set on the front panel, why, and what it looks like when it's wrong. EEPROM access could
reach some of it, but it is undocumented and must never be written.

Possible but untested on the radio: power on/off by CAT (off could strand a remote user), and showing the FM
discriminator and tone-match flags from CAT 0xE7.

## 6. Must be fixed before a public release

1. **No password, and no check on where a request came from.** Anyone on the network can transmit. A web page
   opened on any computer on that network can also send `POST /api/ptt` to the Pi (the bridge takes any content type
   and never looks at Origin or Host). Confirmed 2026-11-24 on a dummy copy: a text/plain request with a foreign Origin keyed it (un-keyed by the heartbeat after 1.2 s). Needs: Origin/Host checks, a panel header that other sites can't send,
   an optional PIN for transmit, and a listen-only role. CAT 4533 and audio 4534 have no password either (WSJT-X can't send
   one): limit them by address.
2. **Installer opens firewall ports 4533 and 4534 but not 8080**, so the panel is blocked on a Pi with ufw on.
3. ~~Personal data~~ done 2026-11-26: the repeater list, Wi-Fi tool, home address and site references are out of git.
4. ~~Never ship build files; licence; credits~~ done 2026-11-26: `.gitignore`, GPL-3.0, credits in the README.
5. **Laptop install** needs Homebrew or Python, a Terminal window, and BlackHole / VB-CABLE. No Linux launcher.
6. **Only tested on:** Ubuntu 24.04 + Hamlib 4.5.5 + one FT-897D + Digirig + a Mac. Other Hamlib versions may answer the raw meter
   read differently. Windows voice is untested.
7. Levels, squelch and drive defaults are one radio's numbers: the calibration steps (#42-44) make them portable.

## 7. Suggested grouping (for discussion)

| Page | For | Contents |
|---|---|---|
| **Operate** | everyone | frequency, mode, PTT, meters, waterfall, volume, mute, squelch, one preset list, favorites, record |
| **Audio** | everyone, with "Show advanced" | headset and mic choice, presets, noise reduction, mic gain; advanced: the rest of section 1 |
| **Repeaters** | FM users | list, favorites, import, export |
| **Setup** | first day, then rarely | guided: radio → sound levels → squelch → transmit drive → headset → network → security |
| **Status** | when something is wrong | problems in plain words, connection quality, log, restart, backup, update |

Rules that keep it simple: one place per control; FM controls only in FM; advanced off by default; every slider shows its
normal value and can be put back to it; nothing that can damage a transmission is changeable while keyed.
