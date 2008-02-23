#!/bin/sh
cmd=`basename "$0"`

# default suffix translation
oldsfx=`expr "x$cmd" : 'x\(.*\)2.*'`
newsfx=`expr "x$cmd" : 'x.*2\(.*\)'`

# per-conv command lines
case "$cmd" in
   bmp2png)	shcmd='bmptoppm | pnmtopng' ;;
   bmp2jpg)	shcmd='cjpeg' ;;
   tif2jpg)	shcmd='tifftopnm | cjpeg' ;;
   pnm2jpg)	shcmd='cjpeg' ;;
   png2jpg)	shcmd='pngtopnm | cjpeg' ;;
   jpg2pnm)	shcmd='djpeg' ;;
   *)		echo "$cmd: unknown conversion" >&2; exit 2 ;;
esac

exec shconv "$oldsfx" "$newsfx" "$shcmd" ${1-.} ${1+"$@"}
