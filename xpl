#!/bin/sh
#
# Run xplanet with the usual options, all command line driven.
#	- Cameron Simpson <cs@zip.com.au> 13jul2004
#

: ${TMPDIR:=/tmp}
: ${XPLANETDIR:=$HOME/.xplanet}
: ${XPLANETIMPATH:=$XPLANETDIR/images}

config=$XPLANETDIR/config

cmd=`basename "$0"`
usage="Usage: $cmd [xplanet-options...] [config=value...]
	xplanet-options	Passed to xplanet.
	config=value	Config options as for an xplanet config file.
			Added to the clause named by the most recent -body
			or -target. Prior, to the [default] clause."

trap 'rm -f "$TMPDIR/$cmd$$".*' 0 15

tmpconfig=$TMPDIR/$cmd$$.cfg

badopts=

xplopts=
xplclause=default

>>"$tmpconfig"
[ -s "$config" ] && cat "$config" >>"$tmpconfig"

while :
do
  case $1 in
    [a-z]*=*)	echo "$1" | winclauseappend "$tmpconfig" "$xplclause" ;;
    -1)		xplopts="$xpltops -num_times 1" ;;
    -o)		xplopts="$xplopts -outfile "`shqstr "$2"`; shift ;;
    -target|-body)
		xplclause=$2 xplopts="$xplopts "`shqstr "$1" "$2"`; shift ;;
    -fork|-gmtlabel|-label|-interpolate_origin_file|-light_time\
    	|-make_cloud_maps|-pango|-print_ephemeris|-random|-save_desktop_file\
    	|-tt|-timewarp|-transparency|-utclabel|-version|-vroot|-window\
    	|-xscreensaver)
		xplopts="$xplopts "`shqstr "$1"` ;;
    -[a-z]*)	xplopts="$xplopts "`shqstr "$1" "$2"`; shift ;;
    *)		break ;;
  esac
  shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

set -x
eval "xplanet $xplopts"

exit $?
