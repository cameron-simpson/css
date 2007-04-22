#!/usr/bin/perl
#
# Geometric routines. 2D for the most part.
#	- Cameron Simpson <cs@zip.com.au> 05sep96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

use cs::Geometry::Point;

package cs::Geometry;

sub mkPoint { cs::Geometry::Point::mkPoint(@_) }
sub mkRectangle { cs::Geometry::Rectangle::mkRectangle(@_) }

# compute bounding box for a set of points
# to extend an existing one, simply prepend it to the set of points
sub boundingBox
	{ return undef unless @_;

	  my($p1)=shift;

	  return mkRectangle($p1,0,0) unless @_;

	  my($lx,$ly)=$p1->XY();
	  my($hx,$hy)=($lx,$ly);
	  my($x,$y);

	  while (@_)
		{ ($x,$y)=shift->XY();
		  $lx=::min($lx,$x);
		  $ly=::min($ly,$y);
		  $hx=::max($hx,$x);
		  $hy=::max($hy,$y);
		}

	  mkRectangle(mkPoint($lx,$ly),$hx-$lx,$hy-$ly);
	}

# given two bounding boxes, translate a set of points from
# one to the other
#	scalePoints([from:lx,ly,hx,hy],[to:lx,ly,hx,hy], x1,y1,x2,y2,...)
#
sub scalePoints
	{ my($from,$to)=(shift,shift);
	  my($sx)=($to->[2]-$to->[0])/($from->[2]-$from->[0]);
	  my($sy)=($to->[3]-$to->[1])/($from->[3]-$from->[1]);
	  my($fx,$fy,$tx,$ty)=($from->[0],$from->[1],$to->[0],$to->[1]);

	  my(@scaled);

	  while (@_ >= 2)
		{ push(@scaled,$tx+(shift(@_)-$fx)*$sx);
		  push(@scaled,$ty+(shift(@_)-$fy)*$sy);
		}

	  @scaled;
	}

# find the nearest point to the one given
sub nearest
	{ my($x,$y,$omitself)=(shift,shift,shift);
	  $omitself=0 if ! defined $omitself;
	  my($d,@p);

	  my($px,$py,$d2);

	  # simple linear search
	  POINT:
	    while (defined ($px=shift) && defined ($py=shift))
		{ next POINT if $omitself && $x == $px && $y == $py;
		  $d2=($x-$px)*($x-$px)+($y-$py)*($y-$py);
		  if (! defined $d || $d2 < $d)
			{ @p=($px,$py);
			  $d=$d2;
			  ## warn "nearest($x,$y) (@p): $d2\n";
			}
		}

	  @p;
	}

1;
