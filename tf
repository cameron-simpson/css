#!/bin/sh
#
# Kick off a "tail -f" of a logfile in a new window.
#	- Cameron Simpson <cs@zip.com.au> 80jul2004
#

: ${VARLOG:=$HOME/var/log}
termopts=

cmd=$0
usage="Usage: $cmd [-iconic] logfile..."

[ "x$1" = x-iconic ] && { termopts="$termopts $1"; shift; }

[ $# = 0 ] && { echo "$usage" >&2; exit 2; }

xit=0

for log
do
  case "$log" in
    /* | ./* | ../* ) ;;
    *) log=$VARLOG/$log ;;
  esac

  [ -f "$log" ] || { echo "$cmd: not a file: $log" >&2
		     xit=1
		     continue
		   }

  logname=`echo "$log" | entilde`
  term -n "TAIL $logname" -small $termopts -e tail -f "$log" || xit=1
done

exit $xit
