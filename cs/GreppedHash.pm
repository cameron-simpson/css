#!/usr/bin/perl
#
# A filtered view of a hash.
# Requires a hash ref (for the source array) and a grep function
# which takes (key,entry) as parameters.
# Can take an optional initialiser function which takes (key,entry)
# to insure the grepness of new entries. This will be called from
# the STORE method if the key didn't exist in the grepped array
# beforehand.
#	- Cameron Simpson <cs@zip.com.au> 08dec97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;

package cs::GreppedHash;

sub db
	{
	  my($db)={};

	  tie(%$db, cs::GreppedHash, @_)
		|| die "can't tie (cs::GreppedHash @_)";

	  $db;
	}

sub TIEHASH
	{
	  my($class,$hash,$grep,$init)=@_;
	  $init=sub { 1; } if ! defined $init;

	  my($this)={ IMPL => $hash,
		      GREP => $grep,
		      INIT => $init,
		    };

	  bless $this, $class;
	}

sub KEYS
	{ my($this)=@_;
	  my($impl)=$this->{IMPL};

	  grep(&{$this->{GREP}}($_,$impl->{$_}),keys %$impl);
	}

sub EXISTS
	{ my($this,$key)=@_;
	  my($impl)=$this->{IMPL};

	  return undef if ! exists $impl->{$key};

	  my($grep)=$this->{GREP};

	  &$grep($key,$impl->{$key});
	}

sub FETCH
	{ my($this,$key)=@_;

	  return undef if ! $this->EXISTS($key);

	  $this->{IMPL}->{$key};
	}

sub STORE
	{ my($this,$key,$value)=@_;
	  my($impl)=$this->{IMPL};

	  if ($this->EXISTS($key))
		{ &{$this->{INIT}}($key,$value);
		  warn "after INIT, grep($key,"
		      .cs::Hier::h2a($value,0)
		      .") fails"
		  if ! &{$this->{GREP}}($key,$value);
		}

	  $impl->{$key}=$value;
	}

sub DELETE
	{ my($this,$key)=@_;

	  delete $this->{IMPL}->{$key}
		if $this->EXISTS($key);
	}

1;
