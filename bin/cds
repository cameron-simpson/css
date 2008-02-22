#!/bin/sh -u
#
# Look for CDs in various places.
#	- Cameron Simpson <cs@zip.com.au> 10arp2006
#

cmd=`basename "$0"` || cmd=$0
usage="Usage: $cmd query"

[ $# = 0 ] && { echo "$usage" >&2; exit 2; }

exec search sanity,musicmatch "$@"
