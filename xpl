#!/bin/sh
#
# Run xplanet with the usual options.
#	- Cameron Simpson <cs@zip.com.au> 13jul2004
#

imdir=$HOME/im
backdrop=$rootbg
projdir=$imdir/projections
clouddir=$projdir
clouds=$clouddir/clouds_2000.jpg
ssecclouds=$clouddir/latest_moll.gif
nightside=$projdir/earthlights-dmsp-big.jpg
dayside=$projdir/earth-2400.jpg

# pick a view on the Sydney side of the planet
lat=`tzcoord.pl Sydney | perl -e '$_=<STDIN>; /^([^.]+)/ || die; srand((time^$$)+getppid()); print int($1-89.5+rand(180))'`

## -demfile "$dem" -grid
## -cloud_ssec "$ssecclouds" \
exec nice xplanet \
	-background "$backdrop" \
	-blend \
	-cloud_image "$clouds" \
	-image "$dayside" \
	-label -labelpos -15-15 \
	-markers \
	-night_image "$nightside" \
	-observer "$lat,0" \
	-radius 40 \
	-root \
	${1+"$@"}
