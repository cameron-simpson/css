#!/bin/sh
[ -t 0 -a -t 1 ] || exec t ${1+"$@"}
exec pageif view-unknown -C ${1+"$@"}
