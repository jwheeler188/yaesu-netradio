#!/bin/zsh
# W5PL FT-897D network radio - lets the web panel's "Start voice client" button start the voice client on this Mac.
# Builds "FT-897 Voice Link.app" in this folder: a tiny AppleScript app that opens voice-from-panel.command when a
# ft897voice:// link is clicked, and registers it with macOS. The browser asks once whether to allow it.
# Changes nothing else. Remove: delete "FT-897 Voice Link.app" (and run this again after moving the folder).
set -e
HERE="${0:A:h}"; APP="$HERE/FT-897 Voice Link.app"
say() { print -P "%F{cyan}==>%f $*"; }
chmod +x "$HERE/voice-from-panel.command" "$HERE/ft897.sh"
rm -rf "$APP"
osacompile -o "$APP" -e 'on open location u' \
  -e 'if u does not start with "ft897voice://" then return' \
  -e 'set f to POSIX path of ((path to me as text) & "::voice-from-panel.command")' \
  -e 'do shell script "open -a Terminal " & quoted form of f' \
  -e 'end open location' \
  -e 'on run' -e 'display dialog "This app starts the FT-897 voice client when you press Start voice client in the web panel. There is nothing to do here." buttons {"OK"} default button 1 with title "FT-897 Voice Link"' -e 'end run'
P="$APP/Contents/Info.plist"; PB=/usr/libexec/PlistBuddy
$PB -c "Set :CFBundleIdentifier net.w5pl.ft897voicelink" "$P" 2>/dev/null || $PB -c "Add :CFBundleIdentifier string net.w5pl.ft897voicelink" "$P"
$PB -c "Add :CFBundleURLTypes array" "$P"
$PB -c "Add :CFBundleURLTypes:0 dict" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLName string FT-897 voice client" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string ft897voice" "$P"
$PB -c "Add :LSUIElement bool true" "$P"
codesign --force --sign - "$APP" 2>/dev/null || true          # (the Info.plist changed: sign it again, ad hoc)
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
say "Done: the web panel's \"Start voice client\" button now starts the voice client on this Mac."
