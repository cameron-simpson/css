#!/usr/bin/perl
#
# Geometric routines for points.
#	- Cameron Simpson <cs@zip.com.au> 12feb98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

use cs::Geometry::Point;

package cs::Geometry::Rectangle;

sub new
	{ my($class,$lxy,$dx,$dy)=@_;
	  bless [ $lxy, mkPoint($dx,$dy) ], $class;
	}
sub mkRectangle { new cs::Geometry::Rectangle(@_); }
sub mkPoint { cs::Geometry::Point::mkPoint(@_); }

sub Dup	{ my($this)=@_;
	  mkRectangle($this->[0],$this->[1]->XY());
	}
sub Move
	{ my($this,$dx,$dy)=@_;
	  $this->[0]->Move($dx,$dy);
	}
sub Origin { shift->[0]; }
sub Size { shift->[1]; }
sub OX	{ shift->[0]->X(); }
sub OY	{ shift->[0]->Y(); }
sub OXY	{ shift->[0]->XY(); }
sub DX	{ shift->[1]->X(); }
sub DY	{ shift->[1]->Y(); }
sub DXY	{ shift->[1]->XY(); }

sub HX	{ my($this)=shift;
	  $this->[0]->X()+$this->DX();
	}
sub HY	{ my($this)=shift;
	  $this->[0]->Y()+$this->DY();
	}
sub HXY	{ my($this)=@_;
	  mkPoint($this->HX(),$this->HY());
	}

1;
