#!/bin/sh
#
# Recursive "g".
#	- Cameron Simpson <cs@zip.com.au> 07aug2001
#

[ $# -gt 0 ] || { echo "Usage: $0 string [find-args...]"; exit 2; }
str=$1; shift

[ $# = 0 ] && set .
case " $*" in *" -"*) ;; *) set "$@" -type f -print ;; esac

find "$@" | xargs g "$str"
