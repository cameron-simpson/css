#!/usr/bin/perl
#
# Common list operations.
#	- Cameron Simpson <cs@zip.com.au> 12mar96
#

use strict qw(vars);

package cs::List;

sub abut
	{ join('',@_);
	}

sub flatten
	{ my(@flat);

	  for (@_)
		{ if (ref eq ARRAY)
			{ push(@flat,@$_);
			}
		  else	{ push(@flat,$_);
			}
		}

	  @flat;
	}

1;
