#!/bin/sh
exec env RXVTOPTS= GUI=lean term -n "E [$dirname]@$HOST $*" -e edit ${1+"$@"}
