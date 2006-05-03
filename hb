#!/bin/sh -u
#
# Wrapper for handbrake, accepting handbrake options and some convenience
# options:
#	--ipod	Default options for iPod conversation at 320x240 resolution.
# - Cameron Simpson <cs@zip.com.au> 25apr2006
#

trace=set-x

cmd=`basename "$0"` || cmd=$0
usage="Usage: $cmd [--ipod] [handbrake-options...]"

hbopts="-2"
[ -t 2 ] && hbopts="-v $hbopts"

badopts=

while [ $# -gt 0 ]
do
  case $1 in
    --ipod)	hbopts="$hbopts -f mp4 -E faac -w 320 -l 240 -b 400 -a 1 -R 48000 -B 160"
		;;
    -[v2dg] \
    | --verbose | --two-pass | --deinterlace | --grayscale )
		hbopts="$hbopts $1"
		;;
    -[CfiotcaseErRbqSBwl] \
    | --format | --input | --output | --title | --chapters \
    | --audio | --subtitle | --encoder | --aencoder \
    | --vb | --quailty | --size | --ab \
    | --width | --height | --crop )
		hbopts="$hbopts $1 $2"
		shift
		;;
    --)		shift
    		break
		;;
    -?*)	echo "$cmd: unrecognised option: $1" >&2
		badopts=1
		;;
    *)		break
		;;
  esac
  shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

exec $trace handbrake $hbopts
