#!/bin/sh
[ -t 0 -a -t 1 ] || exec t ${1+"$@"}
exec view-unknown -C ${1+"$@"}
