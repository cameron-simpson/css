#!/bin/sh

[ $# = 0 ] && { echo "Usage: $0 string [files...]" >&2; exit 2; }

nflag=
[ $# = 1 ] || nflag=-n

[ $# = 1 -a -t 0 ] && { echo "$0: I expect filenames if stdin is a tty!" >&2
			exit 2
		      }

[ -t 1 ] || exec fgrep -i $nflag ${1+"$@"}
fgrep -i $nflag ${1+"$@"} | colour_highlight cyan "$1"
