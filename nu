#!/bin/sh

case $# in
    0)	echo "$0: missing URL" >&2; exit 2 ;;
    1)	;;
    *)	url=$1; shift; set x -t "$*" "$url"; shift ;;
esac

( clear
  noteurl ${1+"$@"}
) &
