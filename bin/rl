#!/bin/sh

cmd=`basename "$0"`
usage="Usage: $cmd [-v] [-l login] [-xh] [--] hosts...
	-v	Verbose.
	-l login Login name at remote end.
	-xh	Add the target hosts to the xhosts list."

badopts=
verbose=
login=
xh=
while :
do
    case $1 in
	--)	shift; break ;;
	-v)	verbose=1 ;;
	-l)	login="-l $2"; shift ;;
	-xh)	xh=1 ;;
	-*)	echo "$cmd: unrecognised option \"$1\" ignored" >&2
		badopts=1
		;;
	*)	break ;;
    esac
    shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

for host
do
    [ $verbose ] && echo "$host ..." >&2
    [ $xh ] && xhost "+$host"
    rlogin "$host" $login
done
