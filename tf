#!/bin/sh
#
# Kick off a "tail -f" of a logfile in a new window.
#	- Cameron Simpson <cs@zip.com.au> 80jul2004
#

: ${VARLOG:=$HOME/var/log}

cmd=$0
usage="Usage: $cmd logfile"

[ $# = 1 ] || { echo "$usage" >&2; exit 2; }
log=$1; shift

case "$log" in
  /* | ./* | ../* ) ;;
  *) log=$VARLOG/$log ;;
esac

[ -f "$log" ] || { echo "$cmd: not a file: $log" >&2; exit 1; }

logname=`echo "$log" | entilde`
exec term -n "TAIL $logname" -e tail -f "$log"
