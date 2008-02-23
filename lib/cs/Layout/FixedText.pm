#!/usr/bin/perl
#
# Layout rules for fixed text (1 char == 1 pixel).
# Actually, we return a ProportionalText object and make a FixedText
# object which acts as a simple font for the ProportionalText.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

use cs::Layout::ProportionalText;

package cs::Layout::FixedText;

sub new
	{ my($class,@strings)=@_;

	  my($font)=bless {}, $class;

	  new cs::Layout::ProportionalText ($font,@strings);
	}

sub Width
	{ shift; length $_[0];
	}

sub Height
	{ 1;
	}

1;
