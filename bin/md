#!/bin/sh

xit=0

for d
do [ -d "$d/." ] || mkdir "$d" || { xit=1; continue; }
   b=`basename "$d"`
   ( set x `ls -d "$b"[0-9]*.* "$b"-*.* 2>/dev/null`; shift
     [ $# = 0 ] && { set x "$b"?*; shift; }
     mrg "$d" "$@"
     rmdir "$d" 2>/dev/null
   ) &
done

wait

exit $xit
