#!/bin/sh
#
# Query or hack the live X resource db.
#	- Cameron Simpson <cs@zip.com.au>
#

: ${TMPDIR:=/tmp}

doedit=

case "$1" in
    -e)	doedit=1; shift ;;
esac

[ $doedit ] || exec xrdb -query ${1+"$@"}

tmp=$TMPDIR/xq.$$
xrdb -query ${1+"$@"} >$tmp || exit $?

[ $# = 0 ] && { set x ${EDITOR-vi}; shift; }
"$@" $tmp

xit=0
xrdb -load - <$tmp || xit=$?
rm -f $tmp

exit $xit
