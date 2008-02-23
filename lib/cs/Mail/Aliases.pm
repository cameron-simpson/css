#!/usr/bin/perl
#
# Handle mail aliases.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Mail::Aliases;

@cs::Mail::Aliases::ISA=qw();

sub db
{ my($path,$rw)=@_;
  $rw=0 if ! defined $rw;

  ::need(cs::Persist);
  my($db)=cs::Persist::db($path,$rw);

  return undef if ! defined $db;

  my($adb)=new cs::Mail::Aliases;

  $adb->AddDB($db);

  $adb;
}

sub new
{ my($class)=@_;
  die if @_ != 1;

  my($this)={ ALIASES => {},
	      DBS     => [],
	    };

  bless $this, $class;
}

sub Value
{ my($this,$key)=@_;

  my($a);

  return undef if ! defined ($a=$this->Alias($key));

  $a->{VALUE};
}

sub Context
{ my($this)=@_;

  return "" if ! exists $this->{CONTEXT};
  $this->{CONTEXT};
}

sub newEntry
{ my($value,$context)=@_;

  my($this)={ VALUE => $value };

  $this->{CONTEXT}=$context if defined $context && length $context;

  bless $this, cs::Mail::Aliases;
}

sub AddDB
{ my($this,@db)=@_;

  my $pushdb = {};

  for my $db (@db)
  { ::addHash($pushdb,$db);
  }

  push(@{$this->{DBS}},$pushdb);
}

sub Add
{ my($this,$key,$value,$force,$context)=@_;
  $force=0 if ! defined $force;

  if ($force || ! exists $this->{ALIASES}->{$key})
  { $this->{ALIASES}->{$key}=newEntry($value,$context);
  }
}

sub implAliases
{ my($key,$entry)=@_;
  ## warn "e=$entry\n";

  return {} if ! exists $entry->{EMAIL};

  my $impl = {};

  my($addrs)=$entry->{EMAIL};
  my(@addrs)=keys %$addrs;
  ## warn "$key: [@addrs]\n";
  ## warn "addrs=".cs::Hier::h2a($addrs,1)."\n";
  ## warn cs::Hier::h2a($entry,1)."\n";

  my($aval,@sfx,$addrtext);

  my $n = 0;

  for my $addr (sort @addrs)
  { $aval=$addrs->{$addr};
    if (! ref $aval)
    { @sfx=$aval;
    }
    elsif (exists $aval->{TAGS})
    { @sfx=@{$aval->{TAGS}};
    }
    else
    { @sfx=();
    }

    @sfx=grep(! /OLD/ && ! /BOGUS/, @sfx);
    if (! @sfx)
    { $n++;
      @sfx=".$n";	# ($n > 1 ? $n : '');
    }

    $addrtext=$entry->AddrText($addr);

    ## warn "$key => $addrtext\n";
    for my $sfx (@sfx)
    {
      ## warn "$key$sfx => $addrtext\n";
      if (! exists $impl->{$key.$sfx})
      { $impl->{$key.$sfx}=$addrtext;
      }

      if (! exists $impl->{$key}
       && $sfx !~ /^\.\d+$/)
      { $impl->{$key}=$addrtext;
      }
    }
  }

  $impl;
}

sub Expand
{ my($this,$text)=@_;
  $this->_Expand($text,{});
}

sub _Expand
{ my($this,$text,$live)=@_;

  my $ndx = cs::RFC822::addrSet($text);

  my $useaddr = {};
  my $purge;

  for my $addr (keys %$ndx)
  { if ($purge=($addr =~ /^!\s*/))
    { $addr=$';
    }

    my $exp = $this->_ExpAlias($addr,$live);
    if (! $exp)
    # $addr isn't an alias name - use as-is
    { $exp={};
      $exp->{$addr}=$ndx->{$addr};
    }

    # array of expanded addresses
    if ($purge)
    { ::subHash($useaddr,$exp);
    }
    else
    { ::addHash($useaddr,$exp);
    }
  }

  $useaddr;
}

sub _ExpAlias
{ my($this,$addr,$live)=@_;

  # loop detection
  return 0 if exists $live->{$addr};

  my($a)=$this->Alias($addr);

  return 0 if ! defined $a;

  $live->{$addr}=1;

  my($exp)=$this->_Expand($a->{VALUE},$live);

  delete $live->{$addr};

  $exp;
}

sub Aliases
{ my($this)=@_;

  my(%hash);

  map($hash{$_}=1, keys %{$this->{ALIASES}});

  for my $db (@{$this->{DBS}})
  { map($hash{$_}=1, keys %$db);
  }

  keys %hash;
}

# get alias record, or undef
sub Alias
{ my($this,$alias)=@_;

  my($A)=$this->{ALIASES};

  if (exists $A->{$alias})
  { return undef if ! defined $A->{$alias};
    return $A->{$alias};
  }

  DB:
  for my $db (@{$this->{DBS}})
  {
    next DB if ! exists $db->{$alias};

    # cache it and return
    my($v)=$db->{$alias};
    $v={VALUE => $v} if ! ref $v;
    $A->{$alias}=bless $v, cs::Mail::Aliases;
    return $A->{$alias};
  }

  return undef;
}

1;
