#!/bin/sh
#
# Recursive "g".
#	- Cameron Simpson <cs@zip.com.au> 07aug2001
#

cmd=$0
grep=fgrep

[ "x$1" = "x-g" ] && { grep=$2; shift; shift; }

[ $# -gt 0 ] || { echo "Usage: $cmd [-g grep] string [find-args...]"; exit 2; }
str=$1; shift

[ $# = 0 ] && set .
case " $*" in *" -"*) ;; *) set "$@" -type f -print ;; esac

find "$@" | xxargs g -g "grep" "$str"
