#!/usr/bin/perl
#
# Index data keyed on MD5 hashes.
#	- Cameron Simpson <cs@zip.com.au> 06jun1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::HASH;
use cs::MD5;

package cs::MD5Index;

@cs::MD5Index::ISA=(cs::HASH);

%cs::MD5Index::_Hashed=();

sub new
	{ my($class)=shift;
	  my($h)={};

	  tie (%$h, $class, @_)
		|| return undef;

	  $h;
	}

sub TIEHASH
	{ my($class,$impl)=(shift,shift);

	  my($this)={};

	  if (! ref $impl)
		{
		  my($h)={};

		  if (defined $impl)
			{ ::need(cs::Persist);
			  tie (%$h, cs::Persist, $impl, @_)
				|| return undef;
			  
			  $this->{FINISH}=$impl;
			}

		  $impl=$h;
		}

	  $this->{IMPL}=$impl;

	  bless $this, $class;
	}

sub DESTROY
	{
	  my($this)=@_;

	  delete $this->{IMPL};

	  if (exists $this->{FINISH})
		{ ::need(cs::Persist);
		  cs::Persist::finish($this->{FINISH});
		}

	  SUPER::DESTROY(@_);
	}

sub _md5
	{ my($key)=@_;
	  return $cs::MD5Index::_Hashed{$key}
		if exists $cs::MD5Index::_Hashed{$key};

	  my($hash);

	  $hash=cs::MD5::md5string($key);

	  return undef if ! defined $hash;

	  $cs::MD5Index::_Hashed{$key}=$hash;
	}

sub KEYS
	{ my($this)=@_;

	  my($impl)=$this->{IMPL};
	  my(@keys)=();

	  my($k1,$k2);

	  for $k1 (keys %$impl)
		{ for $k2 (keys %{$impl->{$k1}})
			{ push(@keys,keys %{$impl->{$k1}->{$k2}});
			}
		}

	  @keys;
	}

sub _hashkey
	{ my($key)=@_;
	  my($md5)=_md5($key);	die "_md5 fails" if ! defined $md5;

	  $md5 =~ /^../ ? ($&,$') : $md5;
	}

sub EXISTS
	{ my($this,$key)=@_;

	  my($impl)=$this->{IMPL};
	  my(@k)=_hashkey($key);

	  my($k);

	  while (@k)
		{ $k=shift(@k);
		  return 0 if ! exists $impl->{$k};
		  $impl=$impl->{$k};
		}

	  1;
	}

sub FETCH
	{ my($this,$key)=@_;

	  my($impl)=$this->{IMPL};
	  my(@k)=_hashkey($key);

	  my($k);

	  while (@k)
		{ $k=shift(@k);
		  return undef if ! exists $impl->{$k};
		  $impl=$impl->{$k};
		}

	  return undef if ! exists $impl->{$key};

	  $impl->{$key};
	}

sub STORE
	{ my($this,$key,$value)=@_;

	  my($impl)=$this->{IMPL};
	  my(@k)=_hashkey($key);

	  my($k);

	  while (@k)
		{
		  $k=shift(@k);
		  $impl->{$k}={} if ! exists $impl->{$k};
		  $impl=$impl->{$k};
		}

	  $impl->{$key}=$value;

	  $value;
	}

1;
