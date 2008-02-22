#!/usr/bin/perl
#
# Do completion on a string.
#	- Cameron Simpson <cs@zip.com.au> 18may98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Complete;

sub new
	{ my($class,$matchset,$matchfn)=@_;
	  $matchfn=\&_isMatch if ! defined $matchfn;

	  bless { FN	=> $matchfn,
		  SET	=> $matchset,
		}, $class;
	}

sub Match
	{ my($this,$prefix,$set,$fn)=@_;
	  $set=$this->{SET} if ! defined $set;
	  $fn=$this->{FN} if ! defined $fn;

	  grep(&$fn($prefix,$_),@$set);
	}

sub _isMatch
	{ my($substr,$candidate)=@_;
	  substr($candidate,$[,length $substr) eq $substr;
	}

1;
