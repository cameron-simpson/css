#!/bin/sh
#
# Extract various archive formats.
#	- Cameron Simpson <cs@zip.com.au> 17dec2002
#

cmd=`basename "$0"`
usage="Usage: $cmd [-t mimetype] [archive]"

mtype=
[ "x$1" = x-t ] && { mtype=$2; shift; shift; }

[ $# = 0 ] && { set x -; shift; }
[ $# = 1 ] || { echo "$usage" >&2; exit 2; }
file=$1; shift

if [ "x$file" = x- ]
then
    [ -t 0 ] && { echo "$cmd: won't read stdin from a tty" >&2; exit 1; }
    if [ -n "$mtype" ]
    then
	file=/dev/fd/0
    else
	tmpf=${TMPDIR:-/tmp}/$cmd$$
	trap 'rm -f "$tmpf"' 0 1 15
	cat >"$tmpf" || exit 1
    	mtype=`file2mime "$tmpf"` || { echo "$cmd: stdin: no MIME type recognised" >&2; exit 1; }
	exec <"$tmpf"
	rm "$tmpf" && trap '' 0 1 15
	file=/dev/fd/0
    fi
else
    [ -n "$mtype" ] \
    || mtype=`file2mime "$file"` \
    || { echo "$cmd: $file: no MIME type recognised" >&2; exit 1; }
fi

if unpack=`mailcap -s "$file" "$mtype" unpack`
then
    exec sh -c "$unpack"
fi

if decode=`mailcap -s "$file" "$mtype" decode`
then
    sh -c "$decode" | "$0" -
    exit $?
fi

echo "$cmd: unhandled MIME type \"$mtype\"" >&2
exit 1
