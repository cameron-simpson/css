#!/bin/sh
#
# TOC or extract various archive formats.
#	- Cameron Simpson <cs@zip.com.au>
#
# Iterative analysis of extensions; the combinations were getting too numerous.
#	- cameron 22jan1999
#

cmd=`basename "$0"`
usage="Usage: $cmd [-n] [-x] files...
	-1	Only the first file is an archive to open.
		Following files are passed as arguments to the archive tool.
	-n	No action.
	-v	Verbose.
	-x	Show command executed."

trace=:
formopts=
long= verb= vlet=
firstonly=

badopts=
while :
do  case $1 in
	-1)	firstonly=1 ;;
	-n)	trace='set -nv' ;;
	-v)	long=-l verb=-v vlet=v ;;
	-x)	trace='set -x' ;;
	--)	shift; break ;;
	-*)	formopts="$formopts $1" ;;
	*)	break ;;
    esac
    shift
done

[ $# -gt 0 ] || { echo "$cmd: missing files" >&2; badopts=1; }

[ $badopts ] && { echo "$usage" >&2; exit 2; }

case "$cmd" in
    t)	[ $# = 0 ] && exec term		# shorthand for term if no args
    	mode=toc ;;
    v)	mode=view ;;
    x)	mode=extract ;;
    *)	echo "$cmd: who am I? I expected to be \"t\" or \"x\"" >&2
	exit 1
	;;
esac

toc_obj='pageif nm'
extract_obj=
view_obj=$toc_ar

toc_pdf=
extract_pdf=
view_pdf='xpdf /dev/fd/0'

toc_ar='pageif ar t${vlet}'
extract_ar='ar x${vlet}'
view_ar=$toc_ar

toc_cpio='pageif cpio -ic${vlet}t'
extract_cpio='cpio -icd${vlet}m'
view_cpio=$toc_cpio

toc_tar='pageif tar t${vlet}f -'
extract_tar=untar	# or use 'tar xvf -'
view_tar=$toc_tar

toc_jar='pageif jar t${vlet}f /dev/fd/0'
extract_jar='jar x${vlet}f /dev/fd/0'
view_jar=$toc_jar

toc_ogg='pageif ogginfo /dev/fd/0'

toc_uu="egrep $formopts '^(begin|end) '"
extract_uu='uudecode $formopts /dev/fd/0'
view_uu=$toc_uu

toc_lzh='pageif xlharc v $formopts'
extract_lzh='xlharc x $formopts'
view_lzh=$toc_lzh

toc_zip='pageif unzip -l $formopts /dev/fd/0'
extract_zip='unzip -d . $formopts /dev/fd/0'
view_zip=$toc_zip

toc_rpm='pageif rpm $verb -q -p /dev/fd/0 -i -R --provides -l $formopts'
extract_rpm='rpm -U $verb -h $formopts /dev/fd/0'
view_rpm=$toc_rpm

toc_rrd='rrdtool fetch /dev/fd/0 MAX'
view_rrd=$toc_rrd

view_mpg='xine -pf $f'
view_avi='xine -pf $f'
#view_avi='aviplay $f'
view_mov=$view_avi
view_asf=$view_avi
view_vob='xine -pf $f'
view_ram='realplay $f'

view_xanm='xanim $formopts /dev/fd/0'

view_rm='realplay $formopts /dev/fd/0'

view_image='xv $formopts /dev/fd/0'
#view_image='ee $formopts /dev/fd/0'

view_rcp='pilrcui $formopts /dev/fd/0'

view_vrml='gtklookat /dev/fd/0'

toc_swf='pageif swfdump -a -t /dev/fd/0'

view_html='pageif w3m -dump -T text/html /dev/fd/0'

view_msword=catdoc

toc_mbox=mbox-toc
extract_mbox=splitmail

xit=0

abort=
trap 'abort=1' 1 2 15

# juggle args if $firstonly
if [ $firstonly ]
then
    f=$1; shift; fargs=$*
    set x "$f"; shift
else
    fargs=
fi

for f
do  [ $abort ] && exit 1

    echo "$f ..."

    if [ -d "$f/." ]
    then
	ls $long -a "$f/." || xit=1
	continue
    fi

    (
      pipe="<\"\$f\""
      pipeext=
      format=

      # break name up into pieces
      case $f in */*)	b=`basename "$f"` ;;
		 *)	b=$f ;;
      esac
      oIFS=$IFS; IFS=.; set x $b; shift; IFS=$oIFS

      nth=$#
      while [ -z "$format" -a "$nth" -gt 0 ]
      do
	  # get nth arg
	  eval "ext=\$$nth"
	  case "$ext" in
	      *[A-Z]*)	lcext=`echo "$ext"|tr '[A-Z]' '[a-z]'` ;;
	      *)	lcext=$ext ;;
	  esac

	  filt=
	  matched=
	  for cext in "$ext" "$lcext"
	  do
	    case $cext in
	      Z)	filt=zcat ;;
	      z)	filt=unpack ;;
	      gz)	filt=gunzip ;;
	      bz2)	filt=bunzip2 ;;
	      pgp)	filt='pgp -df' ;;
	      tgz|nif)	filt=gunzip format=tar ;;
	      taz)	filt=uncompress format=tar ;;
	      o|so)	format=obj ;;
	      a)	format=ar ;;
	      arc|lzh)	format=lzh ;;
	      swf|ogg|uu|pdf|jar|tar|cpio|rpm|rm|rrd|rcp)
			format=$cext ;;
	      zip|exe)	format=zip ;;
	      mpg|mpeg|mpe) format=mpg ;;
	      vob)	format=vob ;;
	      ram|rm)	format=ram ;;
	      jpg|jpeg|jpe|png|gif|xbm|xpm)
			format=image ;;
	      asf|mov|avi)	format=$cext ;;
	      wrl)	format=vrml ;;
	      html|htm)	format=html ;;
	      doc)	format=msword ;;
	      um)	format=mbox ;;
	      *)	case $b in
			    cpiof*)	format=cpio ;;
			    tarf*)	format=tar ;;
			    *)		continue ;;
			esac
			;;
	    esac
	    matched=1
	    break
	  done

	  [ $matched ] || break	# end of the line

	  [ -n "$filt" ] && { pipe="$pipe$pipeext $filt"
			      pipeext=" |"
			    }

	  # figured the format - bail and handle it
	  [ -n "$format" ] && break

	  # backstep for next pass
	  nth=`expr $nth - 1`
      done

      if [ -z "$format" ]
      then
	  echo "$cmd: $f: unrecognised format, using pageif" >&2
	  formatfilter=pageif
      else
	  eval "formatfilter=\$${mode}_$format"
	  if [ -z "$formatfilter" ]
	  then
	      echo "$cmd: $f: don't know how to $mode format $format, using pageif" >&2
	      formatfilter=pageif
	  fi
      fi

      pipe="$pipe$pipeext $formatfilter $fargs"

      eval "$trace; $pipe"
    ) || xit=1

done

exit $xit
