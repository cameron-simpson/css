#!/bin/sh

cmd=$0
grep=fgrep

[ "x$1" = "x-g" ] && { grep=$2; shift; shift; }

[ $# = 0 ] && { echo "Usage: $cmd [-g grep] string [files...]" >&2; exit 2; }

nflag=
[ $# = 1 ] || nflag=-n

# no files? read from stdin: must not be a tty
[ $# = 1 -a -t 0 ] && { echo "$cmd: I expect filenames if stdin is a tty!" >&2
			exit 2
		      }

[ -t 1 ] || exec "$grep" -i $nflag ${1+"$@"}
"$grep" -i $nflag ${1+"$@"} | colour_highlight cyan "$1"
