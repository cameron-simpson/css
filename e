#!/bin/sh
exec env GUI=lean term -n "E [$dirname]@$HOST $*" -e edit ${1+"$@"}
