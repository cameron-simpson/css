#!/usr/bin/perl
#
# Make a tied hash which is a rekey of an existing hash.
#	- Cameron Simpson <cs@zip.com.au> 24jun98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Misc;

package cs::RemappedHash;

@cs::RemappedHash::ISA=(cs::HASH);

sub remapped
{
  my($thisHash)={};

  my($obj)=tie (%$thisHash, cs::RemappedHash, @_);
  die "$::cmd: TIEHASH(..,cs::RemappedHash,@_) fails"
	if ! defined $obj;

  wantarray
	? ($thisHash,$obj)
	: $thisHash;
}

# make an object to remap %$otherHash
#
#  $mapping may be
#	- a scalar, in which case it's the name of a field of an
#	  element of %$otherHash to use for a key
#	- a hash ref, in which case it's a table mapping external
#	  keys to keys of %$otherHash
#	- a sub ref, in which case it's a method function which is called as
#		method($this,$external-key,@mapArgs)
#	  and is expected to return an array of keys to %$otherHash
#	  which match the external key $key
#
#  $keysfn is a sub ref to a method function called as
#		method($this,@mapArgs)
#     It returns an array of external key values.
#     It may be left out for the first two cases of $mapping (above),
#     though if supplied will override the defaults for those cases.
#     It is mandatory for the third case for $mapping.
#
sub TIEHASH
{ my($class,$otherHash,$mapping,$keysfn,@mapArgs)=@_;
  die "no otherHash!" if ! defined $otherHash;
  die "otherHash should be a hash ref!" if ! ref $otherHash
					|| ::reftype($otherHash) ne HASH;
  die "no mapping!" if ! defined $mapping;

  if (! ref $mapping)
  # assume on field name
  { unshift(@mapArgs,$mapping);
    $mapping=\&_MapByField;
    $keysfn=\&_KeysByField if ! defined $keysfn;
  }
  elsif (::reftype($mapping) eq HASH)
  # assume key => otherKey table
  { unshift(@mapArgs,$mapping);
    $mapping=\&_MapByTable;
    $keysfn=\&_KeysByTable if ! defined $keysfn;
  }

  my($this);

  $this=bless { OTHER	=> $otherHash,
		MAPFN	=> $mapping,
		KEYSFN	=> $keysfn,
		MAPARGS	=> [ @mapArgs ],
		CACHED	=> {},
	      }, $class;

  # warn "this=".cs::Hier::h2a($this,1);
  $this;
}

sub EXISTS
	{ my($this,$key)=@_;
	  
	  my(@matches)=MapKey($this,$key);

	  @matches > 0;
	}

sub FETCH
	{ my($this,$key)=@_;

	  my(@matches)=MapKey($this,$key);

	  return undef if ! @matches;

	  my($otherHash)=$this->{OTHER};
	  my($mkey)=$matches[0];

	  return undef if ! exists $otherHash->{$mkey};

	  $otherHash->{$mkey};
	}

sub STORE
	{ my($this,$key,$value)=@_;

	  my(@matches)=MapKey($this,$key);

	  if (! @matches)
		{ my(@c)=caller;
		  warn "$::cmd: STORE($key): nothing matching, from [@c]";
		}
	  else
	  { my($otherKey)=$matches[0];

	    if (@matches > 1)
		{ my(@c)=caller;
		  warn "$::cmd: STORE($key): taking \"$otherKey\" from [@matches], from [@c]";
		}

	    $this->{OTHER}->{$otherKey}=$value;
	  }

	  $value;
	}

sub DELETE
	{ my($this,$key)=@_;

	  my(@matches)=MapKey($this,$key);
	  my($otherHash)=$this->{OTHER};

	  for my $otherKey (@matches)
		{ delete $otherHash->{$otherKey};
		}

	  FlushCache($this,$key);
	}

sub KEYS
	{ my($this)=@_;

	  return &{$this->{KEYSFN}}($this,@{$this->{MAPARGS}})
		if defined $this->{KEYSFN};

	  my(@c)=caller;
	  die "$::cmd: KEYS: can't compute keys, from [@c]";
	}

sub FlushCache
	{ my($this,@keys)=@_;
	  ## {my(@c)=caller;warn "FlushCache(@_) from [@c]\n";}

	  if (@keys)
		# delete specific keys
		{ my($cache)=$this->{CACHED};
		  map(delete $cache->{$_}, @keys);
		}
	  else
	  # delete all keys
	  { $this->{CACHED}={};
	  }
	}

sub MapKey
	{ my($this,$key)=@_;

	  ## my(@c)=caller;
	  ## warn "MapKey(@_) from [@c]\n";

	  my($cache)=$this->{CACHED};
	  my(@otherKeys)=();

	  if (! exists $cache->{$key})
		{ $cache->{$key}=[ &{$this->{MAPFN}}($this,
						     $key,
						     @{$this->{MAPARGS}})
				 ];
		}

	  wantarray
		? @{$cache->{$key}}
		: ( @{$cache->{$key}}
		    ? $cache->{$key}->[0]
		    : undef
		  )
		  ;
	}

sub _MapByField
	{ my($this,$key,$fieldName)=@_;

	  ## warn "MapByField(@_)\n";

	  my($cache)=$this->{CACHED};

	  if (! exists $cache->{$key}
	   && ! keys %$cache)
		# heuristic
		# only if there are no keys in the cache, rebuild
		{ $this->_UpdCacheByField($fieldName);
	  	  $cache=$this->{CACHED};
		}

	  my(@matches)=( exists $cache->{$key}
			 ? @{$cache->{$key}}
			 : ()
		       );

	  if (! @matches)
		{ return wantarray ? () : undef;
		}

	  return wantarray ? @matches : $matches[0];
	}

sub _UpdCacheByField
	{ my($this,$fieldName)=@_;
	  my($otherHash)=$this->{OTHER};

	  FlushCache($this);
	  my($cache)=$this->{CACHED};	# FlushCache() makes a new one

	  my($op,$fval,@fkeys);
	  my(@okeys)=keys %$otherHash;
	  ## warn "okeys=[@okeys]\n";

	  OTHERKEY:
	    for my $otherKey (@okeys)
		{
		  ## warn "otherKey=[$otherKey]\n";

		  $op=$otherHash->{$otherKey};
		  next OTHERKEY if ! ref $op
				|| ::reftype($op) ne HASH
				|| ! exists $op->{$fieldName};


		  @fkeys=_valKeys($op->{$fieldName});
		  ## warn "fkeys=[@fkeys]\n";

		  for my $fkey (@fkeys > 1 ? ::uniq(@fkeys) : @fkeys)
			{ $cache->{$fkey}=[] if ! exists $cache->{$fkey};
		  	  push(@{$cache->{$fkey}},$otherKey);
			}
		}
	}

sub _valKeys
	{ my($val)=@_;

	  my(@keys);

	  if (! ref $val)
		{ @keys="$val";
		}
	  else
	  {
	    my($reftype)=::reftype($val);

	    if ($reftype eq ARRAY)
		{ @keys=@$val;
		}
	    elsif ($reftype eq HASH)
		{ @keys=keys %$val;
		}
	    else{ @keys="$val";
		}
	  }

	  @keys;
	}

sub _KeysByField
	{ my($this,$fieldName)=@_;
	  my($op);

	  # grab keys from the cache, saves examining otherhash
	  my($cache)=$this->{CACHED};
	  my(%icache);
	  for my $key (keys %$cache)
	  	{ map($icache{$_}=1, @{$cache->{$key}});
		}

	 my(@keys)=keys %icache;

	 my($otherHash)=$this->{OTHER};

	 OTHERKEY:
	  for my $otherKey (keys %$otherHash)
		{ next OTHERKEY if exists $icache{$otherKey};

		  next OTHERKEY if ! defined $otherHash->{$otherKey};
		  $op=$otherHash->{$otherKey};

		  next OTHERKEY if ! ref $op
				|| ! exists $op->{$fieldName}
				|| ! defined $op->{$fieldName};

		  push(@keys,_valKeys($op->{$fieldName}));
		}

	  ::uniq(@keys);
	}

sub _MapByTable
	{ my($this,$key,$table)=@_;

	  return undef if ! exists $table->{$key};

	  ref $table->{$key}
		? @{$table->{$key}}
		: $table->{$key}
		;
	}

sub _KeysByTable
	{ my($this,$table)=@_;

	  keys %$table;
	}

1;
