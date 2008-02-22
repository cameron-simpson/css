#!/usr/bin/perl
#
# Math stuff.
#	- Cameron Simpson <cs@zip.com.au> 31jul96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Math;

$cs::Math::PI=3.141592653585979323;

sub numeric	{ local($a,$b)=@_; $a <=> $b }
sub lexical	{ local($a,$b)=@_; $a cmp $b }

sub deg2rad	{ shift(@_)*$cs::Math::PI/180; }
sub rad2deg	{ shift(@_)*180/$cs::Math::PI; }

sub min	{
	  return undef unless @_;
	  my($min)=shift;
	  my($n);
	  while (@_)
	  { $n=shift;
	    if ($n < $min)	{ $min=$n; }
	  }
	  $min;
	}
sub max	{ return undef unless @_;
	  ## warn "max(@_)";
	  my($max)=shift;
	  my($n);
	  while (@_)
	  { $n=shift;
	    if ($n > $max)	{ $max=$n; }
	  }
	  ## warn "max=$max";
	  $max;
	}

1;
