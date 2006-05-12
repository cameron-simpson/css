#!/bin/sh -u
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

cmd=`basename "$0"` || cmd=xpl
usage="Usage: $cmd [xplanet-options...] [config=value...]
	xplanet-options	Passed to xplanet.
	config=value	Config options as for an xplanet config file.
			Added to the clause named by the most recent -body,
			-target or -clause. Initially, to the [default] clause."

trap 'rm -f "$TMPDIR/$cmd$$".*' 0 1 2 13 15

tmpconfig=$TMPDIR/$cmd$$.cfg

badopts=

bgim=
winmode=
dx= dy=
xplopts=
xplclauses=default
mkimage=
label_string=
body=
origin=
switches=

>>"$tmpconfig"
[ -s "$config" ] && cat "$config" >>"$tmpconfig"

setting()
{
  echo "[$xplclauses] $*" >&2
  for _setting_cl in $xplclauses
  do
    for _setting
    do echo "$_setting"
    done | winclauseappend "$tmpconfig" "$_setting_cl"
  done
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

pickbody()
{
  case $1 in
    random)		_pickbody_from="$stars $planets $moons" ;;
    major)		_pickbody_from="$stars $planets" ;;
    minor)		_pickbody_from=$moons ;;
    planets)		_pickbody_from=$planets ;;
    moons)		_pickbody_from=$moons ;;
    stars)		_pickbody_from=$stars ;;
    sol)		echo sun; return 0 ;;
    luna)		echo moon; return 0 ;;
    terra)		echo earth; return 0 ;;
    perelandra)		echo venus; return 0 ;;
    *)			printf "%s\n" "$1"; return 0 ;;
  esac
  for _pickbody in $_pickbody_from
  do  echo "$_pickbody"
  done | pickn 1 $*
}

# =head1 OPTIONS
#
# The options for I<xpl> are as for xplanet(1)
# with the following additions.
# Firstly, any options in the environment variable C<$XPLANETOPTS>
# are prefixed to the command line arguments.
#

set -- $XPLANETOPTS ${1+"$@"}

while [ $# -gt 0 ]
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
		switches="$switches $1"
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
    [a-z]*=*)	switches="$switches $1"
    		setting "$1" ;;
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
    -1)		xplopts="$xplopts -num_times 1" ;;
    # =item B<-clause> I<names>
    #
    # I<names> is a space or comma separated list,
    # of course usually just a single name.
    # Subsequent B<I<param>=I<value>> configuration directives
    # will be appended to each B<[>I<name>B<]> clause listed.
    # Also the special names B<stars>, B<planets> and B<moons>
    # are expanded to the known bodies of that type.
    # 
    # Also see the B<-body> and B<-origin> options below.
    #
    -clause)	switches="$switches $1 $2"
		xplclauses=
		for xplcname in `echo "$2" | tr , ' '`
		do
		  case "$xplcname" in
		    stars|planets|moons)
		      xplclauses="$xplcauses "`eval echo \\\$$xplcname` ;;
		    *)xplclauses="$xplclauses $xplcname" ;;
		  esac
		done
		shift
		;;
    # =item B<-o> I<pathname>
    #
    # Converted into "B<-output> I<pathname>" and passed to I<xplanet>.
    #
    -o|-output)	xplopts="$xplopts -output "`shqstr "$2"`
		winmode=1
		xplmode=
		shift
		;;

    # =item B<-jpg>|B<-png>
    #
    # Create a JPEG or PNG of the view and print its pathname on standard output.
    #
    -jpg)	mkimage=$TMPDIR/$cmd$$-snap.jpg
		xplopts="$xplopts -num_times 1 -output "`shqstr "$mkimage"`
		winmode=1
		xplmode=
		;;
    -png)	mkimage=$TMPDIR/$cmd$$-snap.png
		xplopts="$xplopts -num_times 1 -output "`shqstr "$mkimage"`
		winmode=1
		xplmode=
		;;

    # =item B<-transpng> I<pathname>
    #
    # Passed to I<xplanet>.
    #
    -transpng)	switches="$switches $1 $2"
		xplopts="$xplopts -transpng "`shqstr "$2"`
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
		switches="$switches $1 $2"
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
    # Converted into "B<-background> I<image>" and passed to I<xplanet>.
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
		switches="$switches $1 $2"
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
    # =item B<-lat> I<latitude>
    #
    # Converted into "B<-latitude> I<latitude>" and passed to I<xplanet>
    # with the same extended functionality as B<-latitude>, below.
    #
    # =item B<-latitude> I<latitude>
    #
    # The I<latitude> is either an ordinary latitude between -90 and 90
    # or the word "B<random>" which picks an arbitrary value in that range.
    #
    -latitude|-lat)
		switches="$switches $1 $2"
		case $2 in
		  random) lat=`seq -90 90 | pickn` ;;
		  *)	  lat=$2 ;;
		esac
		xplopts="$xplopts -latitude \"\$lat\""
		shift
		;;
    # =item B<-longitude> I<longitude>
    #
    # The I<longitude> is either an ordinary longitude between -180 and 180
    # or the word "B<random>" which picks an arbitrary value in that range.
    #
    -longitude)
		switches="$switches $1 $2"
		case $2 in
		  random) long=`seq -180 180 | pickn` ;;
		  *)	  long=$2 ;;
		esac
		xplopts="$xplopts -longitude \"\$long\""
		shift
		;;
    # =item B<-target>|B<-body>|B<-origin> I<name>
    #
    # Passed to I<xplanet>.
    # Additionally,
    # these options change the clause
    # into which subsequent "B<param=>I<value>" directives are inserted.
    # The special names B<stars>, B<planets> and B<moons>
    # clause a random body of that type to be chosen.
    # Also see the B<-clause> option above.
    #
    -target|-body|-origin)
		switches="$switches $1 $2"
		optname=`expr "x$1" : 'x-\(.*\)'`
		case $2 in
		  -*)	name=`expr "x$2" : 'x-\(.*\)'` namepfx=- ;;
		  *)	name=$2 namepfx= ;;
		esac
		xplclauses=`pickbody $name`
		echo "$optname=$xplclauses" >&2
		eval "$optname=\$xplclauses"
		xplopts="$xplopts "`shqstr "$1" "$namepfx$xplclauses"`
		shift
		;;
    # =item B<-label_string> I<string>
    #
    # Passed to I<xplanet>.
    # If not supplied, "I<body> from I<origin>" is used.
    #
    -label_string)
		label_string=$2
		shift
		;;
    # =item B<-searchdir> I<dirpath>
    #
    # Passed to I<xplanet>.
    # Additionally, this path is prepended
    # to the environment variable B<$XPLANETIMPATH>,
    # used to locate short image names.
    #
    -searchdir)	switches="$switches $1 $2"
		XPLANETIMPATH=$2:$XPLANETIMPATH
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
    # =item B<-tr>|B<-transparency>
    #
    # Passes B<-transparency> to I<xplanet> and sets B<-root> mode (above).
    #
    -tr|-transparency)
		switches="$switches $1" xplmode= winmode= xplopts="$xplopts -transparency" ;;
    -fork|-gmtlabel|-label|-interpolate_origin_file|-light_time \
    	|-make_cloud_maps|-pango|-print_ephemeris|-random|-save_desktop_file \
    	|-tt|-timewarp|-utclabel|-version)
		switches="$switches $1" xplopts="$xplopts "`shqstr "$1"` ;;
    -[a-z]*)	switches="$switches $1 $2" xplopts="$xplopts "`shqstr "$1" "$2"`; shift ;;
    *)	echo "$cmd: unrecognised argument: $1" >&2; badopts=1 ;;
    # =back
    #
  esac
  shift
done

[ $badopts ] && { echo "$usage" >&2; exit 2; }

exec 3>&1 1>&2

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

##cat $tmpconfig >&2
[ -n "$label_string" ] || { label_string='%t'; [ -n "$origin" ] && label_string="$label_string from %o"; }
case "$label_string" in
  *'%{switches}'*)
	lh=`expr "x$label_string" : 'x\(.*\)%{switches}.*'`
	rh=`expr "x$label_string" : 'x.*%{switches}\(.*\)'`
	label_string=$lh$switches$rh
	;;
esac

eval "(set -x; exec xplanet -tmpdir \"\$TMPDIR\" -config \"\$tmpconfig\" -label_string \"\$label_string\" $xplmode $xplopts)"
xit=$?

[ -n "$mkimage" ] && echo "$mkimage" >&3

exit "$xit"

# =head1 ENVIRONMENT
#
# $TMPDIR, where temporary files are made.
# Also handed to I<xplanet> as its B<-tmpdir> argument.
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
