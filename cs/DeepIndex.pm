#!/usr/bin/perl
#
# Index data on keys which get broken into chains inside for storage efficiency.
# Subclasses are expected to provide a method KeyChain() to break keys up.
#	- Cameron Simpson <cs@zip.com.au> 06jun1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Persist;

package cs::DeepIndex;

@cs::DeepIndex::ISA=(cs::HASH);

sub TIEHASH
	{ my($class,$dir)=@_;
	  my($this)={ DIR	=> $dir,
		      DB	=> {},
		    };

	  # we really do expect this to be a directory
	  if (! (-d "$dir/." || mkdir($dir,0777)))
		{ warn "$'cmd: can't mkdir($dir): $!";
		  return undef;
		}

	  tie %{$this->{DB}}, cs::Persist, $dir
		|| die "can't tie to $dir: $!";

	  bless $this, $class;
	}

sub finish
	{
	  cs::Persist::finish();
	}

sub KeyChain
	{ my($key)=@_;
	}

sub _Descend	# (rawkey,create) -> (db-ref,key) or ()
	{ my($this,$rawkey,$create)=@_;
	  $create=1 if ! defined $create;
	  my(@keychain)=$this->_KeyChain($rawkey);

	  # enforce depth
	  if (! defined $this->{MINDEPTH})
		{ $this->{MINDEPTH}=@keychain-1;
		  $this->{DB}->{''}->Meta()->{MINDEPTH}=$this->{MINDEPTH};
		}

	  my($db)=$this->{DB};
	  my($key);

	  while (@keychain > 1)
		{ $key=shift(@keychain);
		  warn "db=".cs::Hier::h2a($db,0).", key=[$key]";
		  if (! exists $db->{$key})
			{ return () if ! $create;
			  $db->{$key}={};
			}

		  $db=$db->{$key};
		  return () if ! ref $db;
		}

	  ($db,shift(@keychain));
	}

sub KEYS{ keys %{shift->{DB}}; }
sub EXISTS
	{ my($this,$rawkey)=@_;
	  return $this if $rawkey eq '';

	  my($db,$key)=$this->_Descend($rawkey);
	  return undef if ! defined $db;

	  exists $db->{$key};
	}

sub FETCH
	{ my($this,$rawkey)=@_;
	  return $this if $rawkey eq '';

	  my($db,$key)=$this->_Descend($rawkey);
	  return undef if ! defined $db;

	  return undef if ! exists $db->{$key};

	  $db->{$key};
	}

sub STORE
	{ my($this,$rawkey,$value)=@_;
	  my($db,$key)=$this->_Descend($rawkey,1);
	  return undef if ! ref $db;

	  $db->{$key}=$value;
	}

sub DELETE
	{ my($this,$rawkey)=@_;
	  my($db,$key)=$this->_Descend($rawkey);
	  return undef if ! defined $db;

	  delete $db->{$key};
	}

1;
