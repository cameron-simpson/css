#!/usr/bin/perl
#
# Geometric routines for points.
#	- Cameron Simpson <cs@zip.com.au> 12feb98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Geometry::Point;

sub new
	{ my($class,$x,$y)=@_;
	  bless [$x,$y], $class;
	}
sub mkPoint { new cs::Geometry::Point(@_); }
sub Dup	{ my($this)=@_;
	  mkPoint($this->[0],$this->[1]);
	}
sub Move
	{ my($this,$dx,$dy)=@_;
	  $this->[0]+=$dx;
	  $this->[1]+=$dy;
	}
sub X	{ shift->[0]; }
sub Y	{ shift->[1]; }
sub XY	{ my($this)=shift; ($this->[0],$this->[1]); }

# offset from this to that
sub Diff { my($this,$that)=@_;
	   my($x1,$y1,$x2,$y2)=($this->XY(),$that->XY());
	   mkPoint($x2-$x1, $y2-$y1);
	 }

# distance squared and distance
sub Dist2{ my($this,$that)=@_;
	   my($dx,$dy)=$this->Diff($that)->XY();
	   $dx*$dx+$dy*$dy;
	 }
sub Dist { sqrt(Dist2(@_)); }

1;
