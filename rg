#!/bin/sh
#
# Recursive "g".
#	- Cameron Simpson <cs@zip.com.au> 07aug2001
#

cmd=$0
usage="Usage: $cmd [-g grep] [-l] [-v] string [paths...]"

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

[ $# = 0 ] && set .
case " $*" in *" -"*) ;; *) set "$@" -type f -print ;; esac

find "$@" | xxargs g $flags -g "grep" "$ptn"
