#!/usr/bin/perl
#
# Map a hash across a tree keyed on the hash's key prefixes.
# This lets us partition things into smaller chunks
# while pretending we still have a flat hash.
# No gain on a memory-based hashes, but should have some
# win on a cs::Persist object.
# The default prefixing routine takes the first two
# characters of the key and constructs a single layer,
# intended for smallish hashes with keys most likely
# starting with MD5 checksums.
# The depth and chunk size are controlled by
#	@cs::PfxIndex::DefaultSeq
# which is currently (2), but (2,1) or (2,2,2)
# etc could be used depending on the anticipated data
# set size.
#
# Use:
#    new cs::PfxIndex (\%subhash[,pfxfuncref[,fnargs]])
#	Return implementation object using %subhash for storage.
#	pfxfuncref() takes (key,fnargs) as arguments
#	and returns the prefix keychain - the real key
#	is used beneath this.
#    tie(%hash, cs::PfxIndex, \%subhash[, pfxfuncref[, fnargs]])
#	Tied %hash to %subhash using the prefix scheme.
#    db(\%subhash,[pfxfuncref[,fnargs]])
#	Make and return a hash tied to %subhash.
#
# Example:
#    $db=cs::PfxIndex::db(cs::Persist::db('/path/to/persistent/hash'));
#    
# Caveats:
#	Never change the prefix function for a given hash - you must copy
#	it into a new one.
#	The KEYS() function is expensive, and potentially sucks the whole
#	cs::Persist object into memory. Gotta do better than this somehow.
#
# - Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;

package cs::PfxIndex;

@cs::PfxIndex::ISA=qw(cs::HASH);
@cs::PfxIndex::DefaultSeq=(2,2);

sub db
	{ my($a)={};
	  tie(%$a,cs::PfxIndex,@_);
	  $a;
	}

sub TIEHASH
	{ my($class)=shift;
	  new $class @_;
	}

sub new 
	{
	  ## warn "new(@_)";
	  my($class,$db,$fn)=(shift,shift,shift);
	  die "no db!" if ! defined $db;
	  if (! defined $fn)
		{ $fn=\&_dfltPfxFn;
		}
	  elsif (! ref $fn)
		{ unshift(@_,$fn);
		  $fn=\&_dfltPfxFn;
		}

	  bless { DB	=> $db,
		  FN	=> $fn,
		  FNARGS => [ @_ ],
		}, $class;
	}

sub _dfltPfxFn
	{
	  my($key,@seq)=@_;
	  @seq=@cs::PfxIndex::DefaultSeq if ! @seq;

	  my($okey)=$key;
	  my(@keychain,$len);

	  while (length $key && @seq)
		{ $len=shift(@seq);
		  if (length $key >= $len)
			{ push(@keychain,substr($key,$[,$len));
			  substr($key,$[,$len)='';
			}
		  else	{ push(@keychain,$key);
			  $key='';
			}
		}

	  @keychain;
	}

sub KeyChain
	{ my($this,$key)=@_;
	  my(@keychain)=&{$this->{FN}}($key,@{$this->{FNARGS}});

	  # XXX - fixed number of keys to suport KEYS() function
	  if (! exists $this->{CHAINLENGTH})
		{ $this->{CHAINLENGTH}=@keychain;
		}
	  else
	  { my($clen)=$this->{CHAINLENGTH};

	    if (@keychain > $clen)
		{ @keychain=@keychain[0..$clen-1];
		}
	    else
	    { while (@keychain < $clen)
		{ push(@keychain,$key);
		}
	    }
	  }

	  die "keychain length bug"
		if @keychain != $this->{CHAINLENGTH};

	  @keychain;
	}

sub EXISTS
	{ my($this,$key)=@_;
	  die "no key!" if ! defined $key;

	  my(@keychain)=$this->KeyChain($key);
	  my($db)=$this->{DB};

	  local($_);

	  for (@keychain)
		{ return 0 if ! exists $db->{$_};
		  $db=$db->{$_};
		}

	  exists $db->{$key};
	}

sub FETCH
	{ my($this,$key)=@_;
	  die "no key!" if ! defined $key;

	  my(@keychain)=$this->KeyChain($key);
	  my($db)=$this->{DB};

	  for (@keychain)
		{ return undef if ! exists $db->{$_};
		  $db=$db->{$_};
		}

	  $db->{$key};
	}

sub STORE
	{ my($this,$key,$value)=@_;
	  die "no key!" if ! defined $key;

	  my(@keychain)=$this->KeyChain($key);
	  my($db)=$this->{DB};
	  local($_);

	  for (@keychain)
		{ if (! exists $db->{$_})
			{ $db->{$_}={};
			}

		  $db=$db->{$_};
		}

	  $db->{$key}=$value;
	}

sub KEYS
	{ my($this)=@_;

	  my($clen);

	  if (exists $this->{CHAINLENGTH})
		{ $clen=$this->{CHAINLENGTH};
		}
	  else	{ my(@keychain)=$this->KeyChain('dummy');
		  $clen=@keychain;
		}

	  my(@keys);

	  _getKeys($this->{DB},$clen,\@keys);

	  @keys;
	}

sub _getKeys
	{ my($db,$clen,$pkeys)=@_;
	  ## warn "_getkeys: db=$db, clen=$clen, pkeys=$pkeys";

	  if ($clen == 0)
		{ push(@$pkeys,keys %$db);
		}
	  else
	  { my(@keys);

	    $clen--;
	    for (keys %$db)
		{ _getKeys($db->{$_},$clen,$pkeys);
		}
	  }
	}

1;
