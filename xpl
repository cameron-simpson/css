#!/bin/sh
#
# Run xplanet with the usual options, all command line driven.
#	- Cameron Simpson <cs@zip.com.au> 13jul2004
#

: ${TMPDIR:=/tmp}
: ${XPLANETDIR:=$HOME/.xplanet}
: ${XPLANETIMPATH:=$XPLANETDIR/images}

config=$XPLANETDIR/config
stars=sun
planets='mercury venus earth mars jupiter saturn uranus neptune pluto'
moons='moon
       phobos deimos
       io europa ganymede callisto
       mimas enceladus tethys dione rhea titan hyperion iapetus phoebe
       miranda ariel umbriel titania oberon
       triton nereid
       charon'

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

setting()
{
  echo "[$xplclause] $*" >&2
  for _setting
  do echo "$_setting"
  done | winclauseappend "$tmpconfig" "$xplclause"
}

findim()	# imagebase
{
  for _findim_im
  do
    case "$_findim_im" in
      /* | ./* | ../* )
	_findim_found=$_findim_im
	;;
      *)
	_findim_found=
	for _findim_dir in `unpath "$XPLANETIMPATH"`
	do
	  _findim_subdirs=`find "$_findim_dir/." -type d \( \( -name '.\*' -prune \) -o -print \)`
	  _findim_found=`findimindir "$_findim_im" $_findim_subdirs` && break
	done
	;;
    esac
    [ -n "$_findim_found" ] && echo "$_findim_found"
  done
  set +x
}

findimindir()	# imagebase dirs...
{
  _fiid_im=$1; shift
  for _fiid_dir
  do
    for _fiid_ext in '' .jpg .png
    do
      _fiid_path=$_fiid_dir/$_fiid_im$_fiid_ext
      [ -s "$_fiid_path" ] \
      && { echo "$_fiid_path"
	   return 0
	 }
    done
  done
  return 1
}

while :
do
  case $1 in
    image=* | map=* | night_map=* | cloud_map=* )
		cf=`expr "x$1" : 'x\([^=]*\)=.*'`
		im=`expr "x$1" : 'x[^=]*=\(.*\)'`
		case "$im" in
		  random*)
		    words=`expr "$im" : 'random/*\(.*\)'`
		    im=`rbg -n -1 $words`
		    ;;
		  *)
		    im=`findim "$im"`
		    ;;
		esac
		[ -n "$im" -a -f "$im" ] || { echo "$cmd: can't find image for \"$1\"" >&2
					      badopts=1
					    }
		setting "$cf=$im"
		;;
    [a-z]*=*)	setting "$1" ;;
    -1)		xplopts="$xpltops -num_times 1" ;;
    -o)		xplopts="$xplopts -outfile "`shqstr "$2"`; shift ;;
    -background|-bg)
		case $2 in
		  random*)
		    words=`expr "$2" : 'random/*\(.*\)'`
		    bg=`rbg -n -1 -w $words` \
		    || { echo "$cmd: no background chosen" >&2; badopts=1; }
		    ;;
		  *)
		    bg=$2
		    ;;
		esac
		xplopts="$xplopts -background "`shqstr "$bg"`
		shift
		;;
    -target|-body)
		case $2 in
		  random)
		    xplclause=`
		      for body in $stars $planets $moons
		      do echo "$body"
		      done | pickn 1
		      `
		    ;;
		  major)
		    xplclause=`
		      for body in $stars $planets
		      do echo "$body"
		      done | pickn 1
		      `
		    ;;
		  *)
		    xplclause=$2
		    ;;
		esac
		xplopts="$xplopts "`shqstr "$1" "$xplclause"`
		shift
		;;
    -fork|-gmtlabel|-label|-interpolate_origin_file|-light_time \
    	|-make_cloud_maps|-pango|-print_ephemeris|-random|-save_desktop_file \
    	|-tt|-timewarp|-transparency|-utclabel|-version|-vroot|-window \
    	|-xscreensaver)
		xplopts="$xplopts "`shqstr "$1"` ;;
    -[a-z]*)	xplopts="$xplopts "`shqstr "$1" "$2"`; shift ;;
    *)		break ;;
  esac
  shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

##cat $tmpconfig
set -x
eval "xplanet -config \"\$tmpconfig\" $xplopts"

exit $?
