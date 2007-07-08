#!/bin/sh

: ${COLOUR_HIGHLIGHT:=cyan}

cmd=$0
usage="Usage: $cmd [-g grep] [-l] [-v] string [files...]"

grep=fgrep
flags=

badopts=
while :
do
  case $1 in
    -g) grep=$2; shift ;;
    -[lv]) flags="$flags $1" ;;
    --)	shift; break ;;
    -?*)echo "$cmd: unrecognised option: $1" >&2; badopts=1 ;;
    *)	break ;;
  esac
  shift
done

if [ $# = 0 ]
then
    echo "$cmd: missing string" >&2
    badopts=1
else
    ptn=$1; shift
fi

[ $badopts ] && { echo "$usage" >&2; exit 2; }

# no files? read from stdin: must not be a tty
[ $# = 0 -a -t 0 ] && { echo "$cmd: I expect filenames if stdin is a tty!" >&2
			exit 2
		      }

[ -t 1 ] || exec "$grep" -in $flags "$ptn" ${1+"$@"}
"$grep" -in $flags "$ptn" ${1+"$@"} | colour_highlight "$COLOUR_HIGHLIGHT" "$ptn"
