#!/bin/sh -u
#
# Wrapper for the tdl to-do list app.
#	- Cameron Simpson <cs@zip.com.au> 21jun2004
#

cmd=$0
usage="Usage: $cmd [-v] [-q] [+item] [tdl-args...]"

trace=set-x

finalopts=-a
tdqopt=
verbose=
[ -t 2 ] && set -- -v ${1+"$@"}

while [ $# -gt 0 ]
do
  case $1 in
    -v)	verbose=1 tdqopt= ;;
    -q)	verbose= tdqopt=-q ;;
    --)	shift; break ;;
    -?*)echo "$cmd: unsupported leading option: $1" >&2
	badtops=1
	;;
    *)	break ;;
  esac
  shift
done

[ $verbose ] && { finalopts="$finalopts -v"; shift; }

context=
case $1 in
  +*[^1-9.]* )
  	match=`expr "x$1" : 'x.\(.*\)'`; shift
	context=`tdl ls | grep "$match" | awk 'NR == 1 { print $1 }'`
	[ -z "$context" ] && { echo "$cmd: can't match \"$match\"" >&2
			       exit 1
			     }
	;;
  [A-Z]* )
	match=$1; shift
	context=`tdl ls | grep "$match" | awk 'NR == 1 { print $1 }'`
	[ -z "$context" ] && { echo "$cmd: can't match \"$match\"" >&2
			       exit 1
			     }
	;;
  +[1-9]* )
  	context=`expr "x$1" : 'x.\(.*\)'`
	shift
	;;
esac

[ $badopts ] && { echo "$usage" >&2; exit 2; }

if [ $# -gt 0 ]
then
    case "$1" in
      --?*)	op=`expr "x$1" : 'x--\(.*\)'`; shift ;;
      *)	op=add ;;
    esac
    datespec=
    case "$1" in
      @* ) datespec=$1; shift
    esac

    # join all args for "add"
    [ "x$op" = xadd ] && set -- "$*"

    $trace tdl "$op" $datespec $context ${1+"$@"}
fi

exec tdl ls $finalopts $context
