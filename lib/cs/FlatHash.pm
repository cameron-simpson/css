#!/usr/bin/perl
#
# Make a tied hash which is flattened version of an existing hash.
#	- Cameron Simpson <cs@zip.com.au> 05jul98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Misc;

package cs::FlatHash;

@cs::FlatHash::ISA=(cs::HASH);

sub flattened($$;$)
{
  my($thisHash)={};

  my($obj)=tie (%$thisHash, cs::FlatHash, @_);
  die "$::cmd: TIEHASH(..,cs::FlatHash,@_) fails"
	if ! defined $obj;

  wantarray
	? ($thisHash,$obj)
	: $thisHash;
}

# make an object to flatten %$otherHash
#
sub TIEHASH
{ my($class,$otherHash,$depth,$sep)=@_;
  die "no otherHash!" if ! defined $otherHash;
  die "otherHash should be a hash ref!" if ! ref $otherHash
					|| ::reftype($otherHash) ne HASH;
  die "no depth!" if ! defined $depth;
  $sep='/' if ! defined $sep;

  my($this);

  $this=bless { OTHER	=> $otherHash,
		DEPTH	=> $depth,
		SEP	=> $sep,
	      }, $class;

  ## warn "this=".cs::Hier::h2a($this,1);

  $this;
}

sub _KeySplit
{ my($this,$key)=@_;

  my($depth)=$this->{DEPTH};
  my($sep)=  $this->{SEP};

  my(@splat);
  my($i);

  my($okey)=$key;

  while ($depth > 1 && ($i=index($key,$sep)) >= 0)
  { push(@splat,substr($key,0,$i));
    $key=substr($key,$i+length($sep));
    $depth--;
  }

  push(@splat,$key);

  ## warn "keysplit($okey)=[@splat]";

  @splat;
}

sub FETCH
{ my($this,$key)=@_;

  my($db)=$this->{OTHER};

  my(@k)=$this->_KeySplit($key);

  for my $subkey (@k)
  { return undef if ! ref $db;
    return undef if ::reftype($db) ne HASH;
    return undef if ! exists $db->{$subkey};

    $db=$db->{$subkey};
  }

  $db;
}

sub EXISTS
{ my($this,$key)=@_;

  my($v)=$this->FETCH($key);

  defined $v;
}

sub STORE
{ my($this,$key,$value)=@_;

  ## warn "STORE(@_)\n";

  my($db)=$this->{OTHER};

  my(@k)=$this->_KeySplit($key);
  die "too few components to [$key]" if @k != $this->{DEPTH};

  my($subkey,$v);

  while (@k > 1)
  { $subkey=shift(@k);
    if (! exists $db->{$subkey})
    { $db->{$subkey}={};
      $db=$db->{$subkey};
    }
    else
    { $v=$db->{$subkey};
      if (! ref $v
       || ::reftype($v) ne HASH)
      { my(@c)=caller;
	die "$this isn't that deep from [@c]";
      }

      $db=$v;
    }
  }

  $db->{$k[0]}=$value;
}

sub DELETE
{ my($this,$key)=@_;

  my($db)=$this->{OTHER};

  my(@k)=$this->_KeySplit($key);
  die "too few components to [$key]" if @k != $this->{DEPTH};

  my($subkey,$v);

  while (@k > 1)
  { $subkey=shift(@k);
    if (! exists $db->{$subkey})
    { return;	# ==> already deleted
    }
    else
    { $v=$db->{$subkey};
      if (! ref $v
       || ::reftype($v) ne HASH)
      { my(@c)=caller;
	die "$this isn't that deep from [@c]";
      }

      $db=$v;
    }
  }

  delete $db->{$k[0]};
}

sub KEYS
{ my($this)=@_;

  _deepKeys($this->{OTHER},$this->{DEPTH},$this->{SEP});
}

sub _deepKeys($$$);
sub _deepKeys($$$)
{ my($db,$depth,$sep)=@_;

  die if $depth < 1;

  my(@k)=keys %$db;

  $depth--;

  return @k if $depth == 0;

  my(@subk,$v);

  for my $key (@k)
  { $v=$db->{$key};
    if (ref $v && ::reftype($v) eq HASH)
    { push(@subk,
	   map("$key$sep$_",_deepKeys($v,$depth,$sep)));
    }
  }

  @subk;
}

1;
