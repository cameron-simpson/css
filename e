#!/bin/sh
#
# As with t, v and x, edit a file.
#	- Cameron Simpson <cs@zip.com.au> 04may2002
#

: ${dirname:=${PWD:-`pwd`}}
: ${HOST:=`hostname | sed 's/\\.//'`}

exec \
term \
     -n "E [$dirname]@$HOST $*" \
     -e edit ${1+"$@"}
