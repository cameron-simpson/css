#!/usr/bin/perl
#
# Class to manage a rectangular view of some text.
#	- Cameron Simpson <cs@zip.com.au> 21aug96
#

use strict qw(vars);

package cs::TextView;

sub new
	{ my($class,$height,$width)=@_;

	  if (! $height)	{ $height=4; }
	  if (! $width)		{ $width=80; }

	  bless { HEIGHT	=> $height,
		  WIDTH		=> $width,
		  LINES		=> [],
		}, $class;
	}

sub Height
	{ my($this,$h)=@_;

	  if ($h)
		{ $this->{HEIGHT}=$h;
		}
1;
