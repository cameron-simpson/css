#!/usr/bin/perl
#
# cs::DBI::Table::Hash: Treat an indexed DBI table as a hash.
#	- Cameron Simpson <cs@zip.com.au> 25feb2000
#

=head1 NAME

cs::DBI::Table::Hash - treat an indexed DBI table as a hash

=head1 SYNOPSIS

use cs::DBI::Table::Hash;

tie %h, I<dbh>, I<table>, I<keyfield>, I<where>;

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

=head1 OBJECT ATTACHMENT

=over 4

=item tie I<hash>, B<cs::DBI::Table::Hash>, I<dbh>, I<table>, I<keyfield>, I<where>, I<preload>

Attach I<hash> to the I<table> in database I<dbh> with field I<keyfield>
as the hash index.
Preload the table with a single SQL call if I<preload> is true.
I<where> must be an SQL select...where condition
to constrain the records to which I<hash> now applies,
or the empty string.
I<preload> must be true or false (0)
to cause (or not) the table to be preloaded when the hash is created.

=cut

sub TIEHASH($$$$$$)
{ my($class,$dbh,$table,$keyfield,$where,$preload)=@_;

  my $this =
  bless { DBH => $dbh,
	  TABLE => $table,
	  LIVE => {},
	  KEY => $keyfield,
	  WHERE => $where,
	}, $class;


  if ($preload)
  { my $sql = "SELECT * FROM $this->{TABLE}";
    $sql.=" WHERE $this->{WHERE}" if length $this->{WHERE};
    my $sth = cs::DBI::sql($dbh, $sql);
    my $L = $this->{LIVE};
    for my $row (cs::DBI::fetchall_hashref($sth))
    { $L->{$row->{$keyfield}}=$row;
    }
  }

  $this;
}

sub KEYS($)
{ my($this)=@_;

  my $dbh = $this->{DBH};

  my $sql = "SELECT $this->{KEY} FROM $this->{TABLE}";
  $sql.=" WHERE $this->{WHERE}" if length $this->{WHERE};
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

  my $sql = "SELECT * from $this->{TABLE} WHERE $this->{KEY} = ?";
  $sql.=" AND $this->{WHERE}" if length $this->{WHERE};

  my $sth = cs::DBI::sql($this->{DBH},$sql);
  return undef if ! defined $sth;

  my @rows = cs::DBI::fetchall_hashref($sth,$key);
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

# expect key to take on last_id if undef
sub STORE($$$)
{ my($this,$key,$value)=@_;

  my($dbh,$table)=($this->{DBH}, $this->{TABLE});

  $this->DELETE($key) if defined $key;

  cs::DBI::insert($dbh, $table, keys %$value)
  ->ExecuteWithRec($value);

  $key=cs::DBI::last_id() if ! defined $key;

  $this->{LIVE}->{$key}=$value;
}

sub DELETE($$)
{ my($this,$key)=@_;

  # purge old records
  my $sql = "DELETE FROM $this->{TABLE} WHERE $this->{KEY} = ?";
  $sql.=" AND $this->{WHERE}" if length $this->{WHERE};

  cs::DBI::dosql($this->{DBH},$sql,$key);

  delete $this->{LIVE}->{$key};
}

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
