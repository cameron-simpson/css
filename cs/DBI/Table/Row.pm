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
or B<cs::DBI::Table::Array> (this latter is unimplemented).

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

  bless { PARENT => $parent,
	  KEY => $rowkey,
	  DATA => $row,
	}, $class;
}

sub KEYS($)
{ keys %{shift->{DATA}};
}

sub EXISTS($$)
{ my($this,$key)=@_;
  exists $this->{DATA}->{$key};
}

sub FETCH($$)
{ my($this,$key)=@_;

  my $row = $this->{DATA};

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

  my $parent = $this->{PARENT};
  my $sth = cs::DBI::sql($parent->{DBH},
			 "UPDATE $parent->{TABLE} SET $key = ? WHERE $parent->{KEY} = ?");

  if (! defined $sth)
  { warn "$0: STORE($key) into table $parent->{TABLE}: SQL error!";
    return undef;
  }

  if (! $sth->execute($value,$this->{KEY}))
  { warn "$0: STORE($key,$value): error doing SQL!";
  }
  else
  { $this->{DATA}->{$key}=$value;
  }

  $this->{DATA};
}

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
