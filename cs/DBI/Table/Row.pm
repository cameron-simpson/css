#!/usr/bin/perl
#
# cs::DBI::Table::Row: tie a hash to a DBI table row
#	- Cameron Simpson <cs@zip.com.au> 25feb2000
#

=head1 NAME

cs::DBI::Table::Row - tie a hash to a DBI table row

=head1 SYNOPSIS

use cs::DBI::Table::Row;

tie %h, cs::DBI::Table::Row, I<rowref>, I<rowkey>, I<parent>;

=head1 DESCRIPTION

The cs::DBI::Table::Row module provides methods to tie a hash
to a row fetched from a B<cs::DBI::Table::Hash>
or B<cs::DBI::Table::Array>.

I<rowref> is a hashref containing the values of the row.
I<rowkey> is the value for the key field specifying this row.
I<parent> is a reference to the object whose B<FETCH> method obtained the row.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;

package cs::DBI::Table::Row;

require Exporter;

@cs::DBI::Table::Row::ISA=qw(cs::HASH);

sub TIEHASH($$$$)
{ my($class,$row,$rowkey,$parent)=@_;

  ## warn "cs::DBI::Table::Row::TIEHASH(@_)";
  bless { cs::DBI::Table::Row::PARENT => $parent,
	  cs::DBI::Table::Row::KEY => $rowkey,
	  cs::DBI::Table::Row::DATA => $row,
	}, $class;
}

sub _Data   { shift->{cs::DBI::Table::Row::DATA}; }
sub _Key    { shift->{cs::DBI::Table::Row::KEY}; }
sub _Parent { shift->{cs::DBI::Table::Row::PARENT}; }

sub KEYS($)
{ my($this)=@_;

  my $data = $this->_Data();
  ## warn "data=[$data]";

  keys %$data;
}

sub EXISTS($$)
{ my($this,$key)=@_;
  exists $this->_Data()->{$key};
}

sub FETCH($$)
{ my($this,$key)=@_;

  ## warn "FETCH($key)";
  my $row = $this->_Data();

  return undef if ! exists $row->{$key}
	       || ! defined $row->{$key};

  $row->{$key};
}

sub DELETE($$)
{ my($this,$key)=@_;
  $this->STORE($key,undef);
}

sub STORE($$$)
{ my($this,$key,$value)=@_;

  ## warn "STORE(key=$key,value=$value)";
  my $parent = $this->_Parent();
  my $dbh=$parent->_Dbh();
  my $table=$parent->_Table();
  my $keyfield=$parent->_Key();
  my $sqltxt = "UPDATE $table SET $key = ? WHERE $keyfield = ?";
  my $sth = cs::DBI::sql($dbh,"UPDATE $table SET $key = ? WHERE $keyfield = ?");

  if (! defined $sth)
  { warn "$0: STORE($key) into table $table: SQL error!";
    return undef;
  }

  if (! $sth->execute($value,$this->_Key()))
  { my@c=caller;
    warn "$0: STORE($key,$value): error doing SQL!\n\t$sqltxt\n\tfrom [@c]\n\t";
  }
  else
  { $this->_Data()->{$key}=$value;
  }

  $this->_Data();
}

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
