#!/usr/bin/perl
#
# Stuff to fiddle with groups.
#	- Cameron Simpson <cs@zip.com.au> 12dec97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::UNIX::Group;

sub new
	{ my($class)=shift;
	  local($_)=shift;

	  my(@gf);

	  chomp;

	  @gf=split(/:/);

	  bless { CRYPT => $gf[1],
		  GID => $gf[2]+0,
		  MEMBERS => [ grep(length, split(/[\s,]+/,$gf[3])) ],
		  NAME => $gf[0],
		}, $class;
	}

sub GrLine
	{ my($this,$dosort)=@_;
	  $dosort=1 if ! defined $dosort;

	  "$this->{NAME}:$this->{CRYPT}:$this->{GID}:"
	 .join(',', ( $dosort
			? sort @{$this->{MEMBERS}}
			: @{$this->{MEMBERS}} ));
	}

sub Diff	# (g1,g2) => (\@del,\@add)
	{ my($this,$newg)=@_;
	  
	  my(%m1,%m2);

	  for ($this->{MEMBERS})
		{ $m1{$_}=1;
		}

	  for ($newg->{MEMBERS})
		{ $m2{$_}=1;
		}

	  my(@add,@del);

	  @del=sort grep(! $m2{$_}, keys %m1);
	  @add=sort grep(! $m1{$_}, keys %m2);

	  (\@del, \@add);
	}

1;
