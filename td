#!/bin/sh
#
# Wrapper for the tdl to-do list app.
#	- Cameron Simpson <cs@zip.com.au> 21jun2004
#

cmd=$0
usage="Usage: $cmd [-v] [+item] [tdl-args...]"

finalopts=-a
[ "x$1" = x-v ] && { finalopts="$finalopts -v"; shift; }

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

if [ $# -gt 0 ]
then

    op=$1; shift
    case "$op" in
      *\ *)	if [ $# = 0 ]; then set -- "$op"
			   else set -- "$op $*"
	    fi
	    op=add
	    ;;
    esac

    datespec=
    case "$1" in
      @* ) datespec=$1; shift
    esac

    tdl "$op" $datespec $context ${1+"$@"}
fi

exec tdl ls $finalopts $context
