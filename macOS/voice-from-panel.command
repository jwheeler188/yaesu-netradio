#!/bin/zsh
# W5PL FT-897D network radio - started by the web panel's "Start voice client" link (ft897voice://start): the voice
# client like "Start FT-897 Voice", without opening another panel. Set up by install-link.sh.
exec "${0:A:h}/ft897.sh" --voice
