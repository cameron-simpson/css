#!/usr/bin/perl
#
# Hash routines. Glue for things tied to hashes.
#
# This package supplies:
#	FIRSTKEY this
#	NEXTKEY this, lastkey
# It expects the subclass to supply the remaining methods
# and in particular requires:
#	KEYS this
# - Cameron Simpson <cs@zip.com.au> 17may96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::BaseClass;

package cs::HASH;

@cs::HASH::ISA=(cs::BaseClass);

# locate an entry in a hash
sub findEntry
	{ my($db,$key,$field)=@_;

	  if (! defined $field)
		{ return undef if ! exists $db->{$key};
		  return $db->{$key};
		}

	  my($r);

	  for (keys %$db)
		{ $r=$db->{$_};
		  return $r if exists $r->{$field}
				   && $r->{$field} eq $key;
		}

	  undef;
	}

sub DESTROY	{ SUPER::DESTROY(@_); }

sub FIRSTKEY
	{ ## warn "FIRSTKEY(@_)";
	  my($this)=@_;
	  my($meta)=$this;
	  my($each);

	  # reset counter
	  $meta->{EACH}=$each=[0,$this->KEYS()];

	  return undef if @$each == 1;

	  $this->NEXTKEY();
	}

sub NEXTKEY
	{ ## warn "NEXTKEY(@_)";
	  my($this,$lastkey)=@_;
	  my($each)=$this->{EACH};

	  # check for running out of keys
	  return undef if $each->[0]++ >= $#$each;

	  $each->[$each->[0]];
	}


1;
