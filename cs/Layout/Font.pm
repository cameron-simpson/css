#!/usr/bin/perl
#
# Font description.
# Dummied up as for FixedText for now.
#	- Cameron Simpson <cs@zip.com.au> 06jul97
#

use strict qw(vars);

package cs::Layout::Font;

sub new
	{ my($class,$type,$source)=@_;

	  bless {}, $class;
	}

sub Width
	{ my($this,$text)=@_;
	  length($text);
	}

sub Height
	{ 1;
	}

1;
