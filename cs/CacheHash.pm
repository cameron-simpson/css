#!/usr/bin/perl
#
# Cache a hash table.
#	- Cameron Simpson <cs@zip.com.au> 17jun96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::CacheHash;

@cs::CacheHash::ISA=(cs::HASH);

$cs::CacheHash::_cacheSize=16;		# rather arbitrary

# Make a new cache, passed a reference to the hash we're shadowing.
# This will normally be a hash tied to a file or something, since
# there's little point to shadowing another in-memory hash.

sub new { &TIEHASH; }

sub TIEHASH
	{ my($class,$hashref,$cachesize)=@_;

	  if (! defined $cachesize)
		{ $cachesize=$_cacheSize;
		}

	  bless { REAL	=> $hashref,
		  CACHE	=> {},
		  SIZE	=> $cacheSize,
		  USED	=> 0,
		}, $class;
	}

sub DESTROY
	{ my($this)=shift;
	  $DEBUG && print STDERR "CacheHash::DESTROY($this)\n";
	  $this->Sync();
	}

sub MaxSize
	{ my($this,$newsize)=@_;
	  $DEBUG && cs::Upd::err("size max size to $newsize\n");
	  $this->{SIZE}=$newsize;
	}

sub Sync
	{ my($this)=shift;
	  my($k);

	  $DEBUG && cs::Upd::err("Sync: cached = [",
				join('|',$this->Cached()), "]\n");

	  for $k ($this->Cached())
		{ $DEBUG && cs::Upd::err("CH: Sync: check $k\n");
		  $this->_Flush($this->{CACHE}->{$k});
		}
	}

sub Cached
	{ keys %{shift->{CACHE}};
	}

sub _LeastHits
	{ my($this)=shift;
	  my($k,$l,$lh,$p,$ph);

	  for $k ($this->Cached())
		{ $p=$this->{CACHE}->{$k};
		  $ph=$p->{HITS};

		  if (! defined $l
		   || $ph < $lh)
			{ $l=$p;
			  $lh=$ph;
			}
		}

	  $l;
	}

sub FETCH
	{ my($this,$key)=@_;

	  $DEBUG && cs::Upd::err("CH: FETCH($key)\n");

	  return undef if ! $this->EXISTS($key);

	  my($p)=$this->_Fetch($key);

	  $p->{HITS}++;

	  return undef if ! defined $p->{VALUE};

	  $p->{VALUE};
	}

# get reference to cache entry
sub _Fetch
	{ my($this,$key)=@_;
	  my($p);

	  return $this->{CACHE}->{$key}
		if exists $this->{CACHE}->{$key};

	  # not in cache - check lower down
	  if ($DEBUG && ! exists $this->{REAL}->{$key})
		{ warn "call to _Fetch when ! exists REAL{$key}";
		  return undef;
		}

	  while ($this->{USED} >= $this->{SIZE})
		{ $this->_PopCache();
		}

	  $this->{CACHE}->{$key}=_newCache($key,$this->{REAL}->{$key});
	}

sub _Flush
	{ my($this,$that)=@_;

	  if ($that->{CHANGED})
		{ $this->{REAL}->{$that->{KEY}}=$that->{VALUE};
	  	  $DEBUG && cs::Upd::err("_Flush: saved \"$that->{KEY}\"\n");
		  $that->{CHANGED}=0;
		}
	}

sub _Purge
	{ my($this,$that)=@_;
	  $this->_Flush($that);
	  delete $this->{CACHE}->{$that->{KEY}};
	  $DEBUG && cs::Upd::err("_Purge: deleted \"$that->{KEY}\"\n");
	}

# remove the least used item
sub _PopCache
	{ my($this)=shift;
	  my($p)=$this->_LeastHits();

	  $DEBUG && cs::Upd::err("PopCache: p=", cs::Hier::h2a($p), "\n");

	  # sync & delete item
	  $this->_Purge($p);

	  $this->{USED}--;
	}

sub _newCache
	{ my($k,$v)=@_;
	
	  { KEY		=> $k,
	    VALUE	=> $v,
	    CHANGED	=> 0,
	    HITS	=> 0,
	  };
	}

sub EXISTS
	{ my($this,$key)=@_;

	  $DEBUG && cs::Upd::err("CH: EXISTS($key)?\n");

	  return 1 if exists $this->{CACHE}->{$key};

	  $DEBUG && cs::Upd::err("CH: not in cache, checking REAL\n");

	  exists $this->{REAL}->{$key};
	}

sub STORE
	{ my($this,$key,$value)=@_;
	  my($p);

	  $DEBUG && cs::Upd::err("CH: STORE($key,$value)\n");

	  if (exists $this->{CACHE}->{$key})
		{ $p=$this->{CACHE}->{$key};
		  $p->{VALUE}=$value;
		}
	  else
	  { while ($this->{USED} >= $this->{SIZE})
		{ $this->_PopCache();
		}

	    $p=$this->{CACHE}->{$key}=_newCache($key,$value);
	    $this->{USED}++;

	    $DEBUG && cs::Upd::err("saved new entry: ", cs::Hier::h2a($p), "\n");
	    $DEBUG && cs::Upd::err("this=", cs::Hier::h2a($this), "\n");
	  }

	  $p->{CHANGED}=1;
	  $p->{HITS}++;

	  return undef if ! defined $value;

	  $value;
	}

sub DELETE
	{ my($this,$key);

	  if (exists $this->{CACHE}->{$key})
	  	{ delete $this->{CACHE}->{$key};
		  $this->{USED}--;
		}

	  delete $this->{REAL}->{$key};
	}

sub FIRSTKEY
	{ my($this)=shift;
	  $this->{_KEYS}=[ keys %{$this->{REAL}} ];

	  $this->NEXTKEY(undef);
	}

sub NEXTKEY
	{ my($this,$lastkey)=@_;
	  return undef if ! defined $this->{_KEYS}
		       || ! @{$this->{_KEYS}};

	  shift @{$this->{_KEYS}};
	}

1;
