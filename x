#!/bin/sh
#
# Extract various archive formats.
#	- Cameron Simpson <cs@zip.com.au> 17dec2002
#

cmd=`basename "$0"`
usage="Usage: $cmd [archive]"

[ $# = 0 ] && { set x -; shift; }
[ $# = 1 ] || { echo "$usage" >&2; exit 2; }
file=$1; shift

if [ "x$file" = x- ]
then
    [ -t 0 ] && { echo "$cmd: won't read stdin from a tty" >&2; exit 1; }
    tmpf=${TMPDIR:-/tmp}/$cmd$$
    trap 'rm -f "$tmpf"' 0 1 15
    cat >"$tmpf" || exit 1
    exec <&-
    file=$tmpf
fi

mtype=`file2mime "$file"` || { echo "$cmd: $file: no MIME type recognised" >&2; exit 1; }

case "$mtype" in
    application/x-gzip)		gunzip <"$file" | "$0" - ;;
    application/x-bzip)		bunzip2 <"$file" | "$0" - ;;
    application/x-cpio)		cpio -icdv ;;
    application/x-ar)		ar xv "$file" ;;
    application/x-jar)		jar xvf - <"$file" ;;
    application/x-tar)		untar <"$file" ;;
    application/x-uuencode)	uudecode /dev/fd/0 <"$file" ;;
    application/zip|application/x-zip)
				unzip -d . /dev/fd/0 <"$file" ;;
    application/x-lharc)	xlharc x /dev/fd/0 <"$file" ;;
    *)				echo "$cmd: unhandled MIME type \"$mtype\"" >&2
				exit 1
				;;
esac

exit $?
