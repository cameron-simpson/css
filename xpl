#!/bin/sh
#
# Wrapper for xplanet which makes all config file options tunable
# on the command line and provides a number of convenience features.
#	- Cameron Simpson <cs@zip.com.au> 13jul2004
#
# =head1 NAME
#
# xpl - convenient and versatile wrapper for xplanet
#
# =head1 SYNOPSIS
#
# xpl [xplanet-options...] [config=value...] [extra-options...]
#
# =head1 DESCRIPTION
#
# I<xpl> is a wrapper for xplanet(1) which makes all config file options
# tunable on the command line and provides a number of convenience features.
#

: ${TMPDIR:=/tmp}
: ${XPLANETDIR:=$HOME/.xplanet}
: ${XPLANETIMPATH:=$XPLANETDIR/images}
: ${XPLANETOPTS:=-window}	# vs -transparency
: ${XPLANETCONFIG:=$XPLANETDIR/config}

config=$XPLANETCONFIG
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
			Added to the clause named by the most recent -body,
			-target or -clause. Initially, to the [default] clause."

trap 'rm -f "$TMPDIR/$cmd$$".*' 0 15

tmpconfig=$TMPDIR/$cmd$$.cfg

badopts=

bgim=
winmode=
dx= dy=
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
	  _findim_subdirs=`find "$_findim_dir/." -type d \( -name '.\*' -prune -o -print \)`
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

# =head1 OPTIONS
#
# The options for I<xpl> are as for xplanet(1)
# with the following additions.
# Firstly, any options in the environment variable C<$XPLANETOPTS>
# are prefixed to the command line arguments.
#

set -- $XPLANETOPTS ${1+"$@"}

while :
do
  case $1 in
    # =head2 Configuration File Directives
    #
    # I<xpl> takes a copy of your configuration file
    # (specified by the environment variable B<$XPLANETCONFIG>,
    # defaulting to B<$XPLANETDIR/config>)
    # and applies command line settings to it,
    # then runs I<xplanet> with this amended copy.
    # Generally, any command line argument of the form:
    #
    #	param=value
    #
    # is appended to the "current" clause in the config file.
    # Initially this clause is B<[default]>.
    # Use of the B<-body> and B<-origin> options
    # causes the clause to match the chosen body or origin,
    # and subsequent B<I<param>=I<value>> arguments
    # are appended to that new clause.
    # The extra option B<-clause> may also be used
    # to switch clauses without specifying a body or origin.
    #
    # The following directives have extended functionality:
    #
    # =over 4
    #
    # =item B<cloud_map>|B<image>|B<map>|B<night_map>B<=>I<imagefile>
    #
    # In I<xplanet> these directives specify the pathname of an image file.
    # In I<xpl> they may also be simple names like "B<earth>"
    # or "B<mars-thermal>";
    # such a name will be resolved by searching recursively the directories
    # specified by the envionment variable B<$XPLANETIMPATH>
    # for, in the former example, files named "B<earth>", "B<earth.png>"
    # or "B<earth.jpg>".
    # The pathname so found will be written into the configuration.
    #
    # Additionally,
    # any name beginning with "B<random>"
    # will use the I<rbg> script to choose an image
    # using the keywords following the word "B<random>".
    # Example:
    #
    #	xpl -bg 'random nebulae'
    #
    # will use I<rbg> to choose a random image whose pathname
    # includes the word "B<nebulae>".
    #
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
    # =back
    #
    # =head2 Conventional Options
    #
    # I<Xplanet> command line options are passed on to I<xplanet>.
    # Additionally,
    # the following options are extra or have extended functionality:
    #
    # =over 4
    #
    # =item B<-1>
    #
    # Converted into "B<-num_times 1>" and passed to I<xplanet>.
    #
    -1)		xplopts="$xpltops -num_times 1" ;;
    # =item B<-clause> I<name>
    #
    # Subsequent B<I<param>=I<value>> configuration directives
    # will be appened to the B<[>I<name>B<]> clause.
    # Also see the B<-body> and B<-origin> options below.
    #
    -clause)	xplclause=$2; shift ;;
    # =item B<-o> I<pathname>
    #
    # Converted into "B<-output> I<pathname>" and passed to I<xplanet>.
    #
    -o|-output)	xplopts="$xplopts -output "`shqstr "$2"`
		winmode=1
		xplmode=
		shift
		;;
    -transpng)	xplopts="$xplopts -transpng "`shqstr "$2"`
		winmode=1
		shift
		;;
    # =item B<-g> I<geom>
    #
    # Converted into "B<-geometry> I<geom>" and passed to I<xplanet> 
    # with the same extended functionality as B<-geometry>, below.
    #
    # =item B<-geometry> I<geom>
    #
    # Passed to I<xplanet>.
    # Additionally the value "B<screen>" for I<geom>
    # will use the preferred screen backdrop size
    # as returned by the I<bgsize> script.
    # This is handy when using I<xpl> to make screen backdrop images.
    #
    -g|-geometry)
		case "$2" in
		  screen)
		    eval `bgsize -v`
		    ;;
		  *)		
		    dx=`expr "x$2" : 'x\([1-9][0-9]*\)x.*'`
		    dy=`expr "x$2" : 'x[1-9][0-9]*x\([1-9][0-9]*\).*'`
		    ;;
		esac
		xplopts="$xplopts -geometry "`shqstr "${dx}x${dy}"`
		shift
		;;
    # =item B<-bg> I<image>
    #
    # Converted into "B<-background> I<image>" and passed to I<xplanet>
    # with the same extended functionality as B<-background>, below.
    #
    # =item B<-background> I<image>
    #
    # The I<image> specified has the same extended facilities
    # as for the "B<image=>I<name>" etc directives described earlier;
    # searching in B<$XPLANETIMPATH> and the "B<random> I<keywords...>"
    # special name.
    # Additionally,
    # the name "B<last>" may be used to specify the last background
    # chosen with I<xpl>.
    #
    -background|-bg)
		case $2 in
		  last)
		    bgim=`lastvalue xplanetbg`
		    ;;
		  random*)
		    words=`expr "$2" : 'random/*\(.*\)'`
		    bgim=`rbg -n -1 -w $words` \
		    || { echo "$cmd: no background chosen" >&2; badopts=1; }
		    ;;
		  *)
		    bgim=`findim "$2"`
		    ;;
		esac
		xplopts="$xplopts -background \"\$bgim\""
		shift
		;;
    # =item B<-target>|B<-body> I<name>
    #
    # Passed to I<xplanet>.
    # Additionally,
    # these options change the clause
    # into which subsequent "B<param=>I<value>" directives are inserted.
    # Also see the B<-clause> option above.
    #
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
    # =item B<-searchdir> I<dirpath>
    #
    # Passed to I<xplanet>.
    # Additionally, this path is prepended
    # to the environment variable B<$XPLANETIMPATH>,
    # used to locate short image names.
    #
    -searchdir)	XPLANETIMPATH=$2:$XPLANETIMPATH
		xplopts="$xplopts "`shqstr "$1" "$2"`
		shift
		;;
    # =item B<-root>
    #
    # Don't use the B<-vroot>|B<-xscreensaver>, B<-window> or B<-output> modes;
    # handy if you've put such a mode in your B<$XPLANETOPTS> variable
    # (which I do to avoid damaging my root backdrop by accident).
    #
    -root)	xplmode= winmode= ;;
    -window)	xplmode=$1 winmode=1 ;;
    -vroot|-xscreensaver)
		xplmode=$1 winmode= ;;
    -fork|-gmtlabel|-label|-interpolate_origin_file|-light_time \
    	|-make_cloud_maps|-pango|-print_ephemeris|-random|-save_desktop_file \
    	|-tt|-timewarp|-transparency|-utclabel|-version)
		xplopts="$xplopts "`shqstr "$1"` ;;
    -[a-z]*)	xplopts="$xplopts "`shqstr "$1" "$2"`; shift ;;
    # =back
    #
    *)		break ;;
  esac
  shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

if [ -n "$bgim" ]
then
  if [ -z "$dx" ]
  then
    if [ $winmode ]
    then dx=512 dy=512
    else eval `bgsize -v`
    fi
  fi
  bgim=`set -x; mkwall -g "${dx}x${dy}" "$bgim"` \
  && lastvalue xplanetbg "$bgim"
fi

##cat $tmpconfig
eval "(set -x; exec xplanet -config \"\$tmpconfig\" $xplmode $xplopts)"

exit $?

# =head1 ENVIRONMENT
#
# $TMPDIR, where temp files are made
#
# $XPLANETDIR, where configuration files are expected.
# Default: C<$HOME/.xplanet>.
#
# $XPLANETCONFIG, the default base configuration.
# Default: C<$XPLANETDIR/config>.
#
# $XPLANETOPTS, default I<xpl> options prefixed to the command line.
#
# $XPLANETIMPATH, a colon separated list of directories
# to search for image files.
# Default: C<$XPLANETDIR/images>.
# 
# =head1 FILES
#
# $XPLANETDIR/config, the base configuration file
#
# =head1 SEE ALSO
#
# xplanet(1), the program that does the real work:
# http://freshmeat.net/projects/xplanet/
#
# =head1 AUTHOR
#
# Cameron Simpson <cs@zip.com.au> July 2004
#
