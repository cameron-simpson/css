#!/bin/sh
. ./.cgienv.sh
exec cgiwrap -0 "$0" ${1+"$@"}
