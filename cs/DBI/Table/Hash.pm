#!/usr/bin/perl
#
# cs::DBI::Table::Hash: Treat an indexed DBI table as a hash.
#	- Cameron Simpson <cs@zip.com.au> 25feb2000
#

=head1 NAME

cs::DBI::Table::Hash - treat an indexed DBI table as a hash

=head1 SYNOPSIS

use cs::DBI::Table::Hash;

tie %h, I<dbh>, I<table>, I<keyfield>;

=head1 DESCRIPTION

The cs::DBI::Table::Hash module permits you to tie a database
table to a hash.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::HASH;
use cs::DBI;
use cs::DBI::Table::Row;

package cs::DBI::Table::Hash;

require Exporter;

@cs::DBI::Table::Hash::ISA=qw(cs::HASH);

sub TIEHASH($$$$)
{ my($class,$dbh,$table,$keyfield)=@_;

  bless { DBH => $dbh,
	  TABLE => $table,
	  LIVE => {},
	  KEY => $keyfield,
	}, $class;
}

sub KEYS($)
{ my($this)=@_;

  my $dbh = $this->{DBH};

  my $sql = "SELECT $this->{KEY} FROM $this->{TABLE}";
  ## warn "SQL is [$sql]";
  my $sth = cs::DBI::sql($this->{DBH},$sql);
  if (! defined $sth)
  { warn "$0: can't look up keys from $this->{TABLE}";
    return ();
  }

  return () if ! $sth->execute();

  my @keys = ();
  my @r;

  while (@r=$sth->fetchrow_array())
  { push(@keys,$r[0]);
  }

  ::uniq(grep(defined,@keys));
}

sub FETCH($$)
{ my($this,$key)=@_;

  return $this->{LIVE}->{$key}
  if exists $this->{LIVE}->{$key};

  my @rows = cs::DBI::find($this->{DBH},$this->{TABLE},$this->{KEY},$key);

  return undef if ! @rows;

  warn "$0: FETCH($this->{TABLE},$key): multiple hits!"
  if @rows > 1;

  my $row = {};

  tie %$row, cs::DBI::Table::Row, $rows[0], $key, $this;

  $row;
}

sub EXISTS($$)
{ my($this,$key)=@_;
  
   exists $this->{LIVE}->{$key}
|| defined $this->FETCH($key);
}

sub STORE($$$)
{ my($this,$key,$value)=@_;

  my($dbh,$table)=($this->{DBH}, $this->{TABLE});

  $this->DELETE($key);

  cs::DBI::insert($dbh, $table, keys %$value)
  ->ExecuteWithRec($value);

  $this->{LIVE}->{$key}=$value;
}

sub DELETE($$)
{ my($this,$key)=@_;

  # purge old records
  cs::DBI::dosql($this->{DBH},"DELETE FROM $this->{TABLE} WHERE $this->{KEY} = ?", $key);

  delete $this->{LIVE}->{$key};
}

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
