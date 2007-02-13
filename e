#!/bin/sh -u
#
# As with t, v and x, edit a file.
#	- Cameron Simpson <cs@zip.com.au> 04may2002
#

: ${dirname:=${PWD:-`pwd`}}
: ${HOST:=`hostname | sed 's/\\.//'`}

asyncopt=
if [ $# -gt 0 ] && [ "x$1" = x+a ]
then
  asyncopt=+a
  shift
fi

exec \
term $asyncopt \
     -n "E [$dirname]@$HOST $*" \
     -e edit ${1+"$@"}
