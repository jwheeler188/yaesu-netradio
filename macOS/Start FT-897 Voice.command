#!/bin/zsh
# W5PL FT-897D network radio - voice: double-click to start the audio client with your headset (macOS).
# Uses the Mac's current Sound output and input (System Settings > Sound): pick your headset there first.
# The web panel opens by itself; hold PTT (or Space) to talk.
# Radio: use DIG mode (USER-U / USER-L) - the 897 only takes rear DATA-jack audio in DIG/PKT modes.
exec "${0:A:h}/ft897.sh" --voice --open-panel
