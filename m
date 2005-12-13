#!/bin/sh
[ $# = 0 ] && set -- -y
exec mutt ${1+"$@"}
