#!/usr/bin/perl
#
# Layout rules for proportional text.
#	- Cameron Simpson <cs@zip.com.au> 06jul97
#

use strict qw(vars);

package cs::Layout::ProportionalText;

sub new
	{ my($class,$font,@strings)=@_;

	  bless { FONT => $font, TEXT => [ @strings ] }, $class;
	}

sub Width
	{ my($this)=shift;
	  $this->{FONT}->Width(join(" ",@{$this->{TEXT}}));
	}

sub Height
	{ my($this)=shift;
	  $this->{FONT}->Height(join(" ",@{$this->{TEXT}}));
	}

# figure out what will fit into a given space
sub CutToFit
	{ my($this,$width)=@_;

	  my($T)=$this->{TEXT};
	  my($sofar)=$T->[0];
	  my($len)=length $sofar;
	  my($longer,$nlen);
	  my($n)=0;

	  TRY:
	    for (@$T[1..$#$T])
		{ $longer="$sofar $_";
		  $nlen=$this->{FONT}->Width($longer);
		  last TRY if $nlen > $width;
		  $len=$nlen;
		  $sofar=$longer;
		  $n++;
		}

	  my($t)=new cs::Layout::ProportionalText ($this->{FONT},$sofar);

	  return $t if $n == $#$T;

	  my($o)=new cs::Layout::ProportionalText ($this->{FONT},@$T[$n+1..$#$T]);

	  return ($t,$o);
	}

1;
