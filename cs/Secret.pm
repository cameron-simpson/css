#!/usr/bin/perl
#
# Manipulate my "secrets" db.
#	- Cameron Simpson <cs@zip.com.au> 09nov99
#

use strict qw(vars);

use cs::Misc;
use cs::Persist;

package cs::Secret;

$cs::Secret::DBpath = "$ENV{HOME}/private/secret/db";
[ -e $cs::Secret::DBpath ] || ($cs::Secret::DBpath = "$ENV{HOME}/.secret");

sub _db()
{ if (! defined $cs::Secret::_db)
  { $cs::Secret::_db=cs::Persist::db($cs::Secret::DBpath);
  }

  $cs::Secret::_db;
}

sub list()
{ my $db = _db();
  keys %$db;
}

sub get($)
{ my($key)=@_;

  my $db = _db();
  return undef if ! defined $db;

  return undef if ! exists $db->{$key};
  my $s = $db->{$key};
  bless $s;
}

sub new($$)
{ my($class,$key)=@_;

  my $s = get($key);
  if (defined $s)
  { warn "$::cmd: secret \"$key\" already exists!\n";
    return undef;
  }

  my $db = _db();
  $s = $db->{$key} = {};

  bless $s;
}

sub Keys($)
{ my($this)=@_;
  keys %$this;
}

sub Set($$$)
{ my($this,$key,$value)=@_;
  tied(%{_db()})->SetReadWrite(1);
  $this->{$key}=$value;
}

sub Value($$)
{ my($this,$key)=@_;
  return undef if ! exists $this->{$key};
  $this->{$key};
}

1;
