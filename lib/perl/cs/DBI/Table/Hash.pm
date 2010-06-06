#!/usr/bin/perl
#
# cs::DBI::Table::Hash: Treat an indexed DBI table as a hash.
#	- Cameron Simpson <cs@zip.com.au> 25feb2000
#

=head1 NAME

cs::DBI::Table::Hash - treat an indexed DBI table as a hash

=head1 SYNOPSIS

use DBI;
use cs::DBI::Table::Hash;

tie %h, cs::DBI::Table::Hash, I<dbh>, I<table>, I<keyfield>, I<where>;

use cs::DBI;

$hashref=cs::DBI::hashtable(I<dbh>,I<table>,I<keyfield>,I<where>,I<preload>)

=head1 DESCRIPTION

The cs::DBI::Table::Hash module permits you to tie a I<DBI(3)> database
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
  bless { cs::DBI::Table::Hash::DBH => $dbh,
	  cs::DBI::Table::Hash::TABLE => $table,
	  cs::DBI::Table::Hash::LIVE => {},
	  cs::DBI::Table::Hash::KEY => $keyfield,
	  cs::DBI::Table::Hash::WHERE => $where,
	}, $class;


  if ($preload)
  { my $table=$this->_Table();
    my $where=$this->_Where();

    my $sql = "SELECT * FROM $table";
    $sql.=" WHERE $where" if length $where;

    my $sth = cs::DBI::sql($dbh, $sql);

    my $L = $this->{cs::DBI::Table::Hash::LIVE};
    for my $row (cs::DBI::fetchall_hashref($sth))
    { if (defined $row->{$keyfield})
      { $this->_Stash($row->{$keyfield}, $row);
      }
    }
  }

  $this;
}

sub _Table($) { shift->{cs::DBI::Table::Hash::TABLE}; }
sub _Dbh($)   { shift->{cs::DBI::Table::Hash::DBH}; }
sub _Key($)   { shift->{cs::DBI::Table::Hash::KEY}; }
sub _Live($)  { shift->{cs::DBI::Table::Hash::LIVE}; }
sub _Where($) { shift->{cs::DBI::Table::Hash::WHERE}; }

=back

=head1 OBJECT METHODS

In addition to the KEYS, FETCH, STORE etc methods
used to implement the tie,
some other methods are available to do table-related things.

=over 4

=item AutoInsert(I<hashref>)

Add a new record described by I<hashref>
to the table.
This depends upon the key field
being an B<AUTOINCREMENT> value.
The new key field is returned.

=cut

sub AutoInsert($$)
{ my($this,$h)=@_;

  my $keyfield = $this->_Key();

  if (exists $h->{$keyfield})
  { warn "$0: AutoInsert: column \"$keyfield\" discarded";
    delete $h->{$keyfield};
  }

  my $ins = cs::DBI::insert($this->_Dbh(),
			    $this->_Table(),
			    keys %$h);
  if (! defined $ins)
  { warn "$::cmd: can't make cs::DBI::insert()";
    return undef;
  }

  $ins->ExecuteWithRec($h);

  last_id();
}

sub KEYS($)
{ my($this)=@_;

  my $dbh = $this->_Dbh();

  my $table = $this->_Table();
  my $sql = "SELECT ".$this->_Key()." FROM $table";
  my $where = $this->_Where();
  $sql.=" WHERE $where" if length $where;
  ## warn "SQL is [$sql]";

  my $sth = cs::DBI::sql($this->_Dbh(),$sql);
  if (! defined $sth)
  { warn "$0: can't look up keys from $table";
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

sub _Stash($$$)
{ my($this,$key,$rec)=@_;

  my $row = {};

  tie %$row, cs::DBI::Table::Row, $rec, $key, $this;

  $this->_Live()->{$key}=$row;
}

sub FETCH($$)
{ my($this,$key)=@_;

  ## warn "FETCH($key)";

  my $live = $this->_Live();
  return $live->{$key} if exists $live->{$key};
  ## warn "key \"$key\" not in _Live";

  my $table=$this->_Table();
  my $sql = "SELECT * from $table WHERE ".$this->_Key()." = ?";
  my $where = $this->_Where();
  $sql.=" AND $where" if length $where;

  my $sth = cs::DBI::sql($this->_Dbh(),$sql);
  ## warn "failed to make sth from \"$sql\"" if ! defined $sth;
  return undef if ! defined $sth;

  my @rows = cs::DBI::fetchall_hashref($sth,$key);
  ## warn "no hits for \"$key\"" if ! @rows;
  return undef if ! @rows;

  if (@rows > 1)
  {
    ::need(cs::Hier);
    warn "$0: FETCH($table,$key): multiple hits!\n\t".join("\n\t",map(cs::Hier::h2a($_,0), @rows))."\n\t";
  }

  $this->_Stash($key,$rows[0]);
}

sub EXISTS($$)
{ my($this,$key)=@_;
  
   exists $this->_Live()->{$key}
|| defined $this->FETCH($key);
}

# expect key to take on last_id if undef
sub STORE($$$)
{ my($this,$key,$value)=@_;

  my $tkey = $this->_Key();
  $value->{$tkey}=$key if ! exists $value->{$tkey};

  ## warn "STORE($key)=".cs::Hier::h2a($value,0);
  my($dbh,$table)=($this->_Dbh(), $this->_Table());

  $this->DELETE($key) if defined $key;

  ## warn "insert gives ".

  cs::DBI::insert($dbh, $table, keys %$value)
  ->ExecuteWithRec($value);

  $key=cs::DBI::last_id() if ! defined $key;

  $value;
}

sub DELETE($$)
{ my($this,$key)=@_;

  # purge old records
  my $table = $this->_Table();
  my $sql = "DELETE FROM $table WHERE ".$this->_Key()." = ?";
  my $where = $this->_Where();
  $sql.=" AND $where" if length $where;

  cs::DBI::dosql($this->_Dbh(),$sql,$key);

  delete $this->_Live()->{$key};
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
