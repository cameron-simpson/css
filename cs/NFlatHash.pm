#!/usr/bin/perl
#
# Store a hash table in a file.
#	- Cameron Simpson <cs@zip.com.au> 07nov95
#
# Recoded to use CacheHash and RawFlatHash. - cameron, 23jun96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::RawFlatHash;
use cs::CacheHash;

package cs::NFlatHash;

@cs::NFlatHash::ISA=(cs::CacheHash);

sub new { &TIEHASH; }

sub TIEHASH
	{ my($class,$path)=@_;
	  my($raw,$cache);

	  $raw={};
	  tie(%$raw,RawFlatHash,'foo.fh') || return undef;
	  return undef if ! defined ($cache=new cs::CacheHash $raw);

	  bless $cache, $class;
	}

sub NeedReWrite
	{ my($this)=shift;

	  $this->{REAL}->NeedReWrite(@_);
	}

sub DESTROY
	{ $DEBUG && print STDERR "NFlatHash::DESTROY(@_)\n";
	  shift->CacheHash::DESTROY();
	}

1;
