#!/bin/sh -u
#
# Kick off a "tail -f" of a logfile in a new window.
#	- Cameron Simpson <cs@zip.com.au> 80jul2004
#

: ${LOGDIR:=$HOME/var/log}

termopts=
multitail=1

cmd=$0
usage="Usage: $cmd [-iconic] [-T] logfiles...
	-iconic	Start terminal iconifyied.
	-T	Use tail instead of multitail."

badopts=

while [ $# -gt 0 ]
do
  case "$1" in
    -iconic)	termopts="$termopts $1" ;;
    -T)		multitail= ;;
    --)		shift; break ;;
    -?*)	echo "$cmd: unrecognised option: $1" >&2
		badopts=1
		;;
    *)		break ;;
  esac
  shift
done

[ $# = 0 ] && { echo "$cmd: missing logfiles" >&2; badopts=1; }

# rewrite logfiles into full paths
lognames=$*
first=1
for log
do
  case "$log" in
    /* | ./* | ../* ) ;;
    *) log=$LOGDIR/$log ;;
  esac
  [ -f "$log" ] || { echo "$cmd: not a file: $log" >&2
		     badopts=1
		     continue
		   }
  if [ $first ]
  then  first=
	set -- "$log"
  else  set -- "$@" "$log"
  fi
  echo "*=[$*]"
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

if [ $multitail ]
then
  exec term -n "TAIL $lognames" -small $termopts \
	    -e multitail -M 10240 -b 8 -x 'MTAIL %t %f' "$@"
fi

xit=0

for log
do
  logname=`echo "$log" | entilde`
  term -n "TAIL $logname" -small $termopts -e tail -f "$log" || xit=1
done

exit $xit
