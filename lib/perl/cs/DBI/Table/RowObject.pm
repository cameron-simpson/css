#!/usr/bin/perl
#
# cs::DBI::Table::RowObject: a base class for objects wrapping a table row.
#	- Cameron Simpson <cs@zip.com.au> 10mar2000
#

=head1 NAME

cs::DBI::Table::RowObject - base class for objects wrapping a table row.

=head1 SYNOPSIS

use cs::DBI::Table::RowObject;

=head1 DESCRIPTION

The cs::DBI::Table::RowObject module provides a base class for objects
which embody a row from a table.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::DBI;

package cs::DBI::Table::RowObject;

require Exporter;

@cs::DBI::Table::RowObject::ISA=qw();

=head1 OBJECT CREATION

=over 4

=item fetch cs::DBI::Table::RowObject (I<keyvalue>,I<dbh>,I<table>,I<keyfield>,I<where>,I<preload>)

Return a B<cs::DBI::Table::RowObject> representing the row
whose I<keyfield> matches I<keyvalue>
from the table specified by I<dbh>, I<table> and I<where>
as for the B<cs::DBI::hashtable> function.
This object is suitable for subclassing
by a table specific module.

=cut

sub fetch($$$$$;$$)
{ my($class,$keyvalue,$dbh,$table,$keyfield,$where,$preload)=@_;

  if (! defined $keyvalue)
  { my@c=caller;
    die "$0: no keyvalue supplied to fetch()\n\tfrom [@c]\n\t";
  }

  my $H = cs::DBI::hashtable($dbh,$table,$keyfield,$where,$preload);
  die "$0: can't obtain hashtable($dbh,$table,$keyfield,$where,$preload)"
  if ! defined $H;

  return undef if ! exists $H->{$keyvalue};

  bless { cs::DBI::Table::RowObject::DATA => $H->{$keyvalue} }, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item _Data()

Return the hashref to the actual row data
(tied to a B<cs::DBI::Table::Row> object).

=cut

sub _Data($) { shift->{cs::DBI::Table::RowObject::DATA}; }

=item Fields()

Return the field names in this row.

=cut

sub Fields($)
{ my($this)=@_;
  return keys %{$this->_Data()};
}

=item Flags()

Return a B<cs::Flags> object containing the project's flags.
Changes made to this object are not reflected in the database
until the B<SaveFlags> method (below) is called.

=cut

sub Flags($)
{ my($this)=@_;

  if (! exists $this->{cs::DBI::Table::RowObject::FLAGS})
  { ::need(cs::Flags);
    my $flags = $this->GetSet(FLAGS);
    $this->{cs::DBI::Table::RowObject::FLAGS}
    = cs::Flags->new(defined($flags)
		     ? grep(length, split(/[,\s]+/, $flags))
		     : ());
  }

  my $flags = $this->{cs::DBI::Table::RowObject::FLAGS};

  return $flags->Members() if wantarray;

  return $flags;
}

=item SaveFlags()

Bring the database into sync with the flags stored in the user object.

=cut

sub SaveFlags($)
{ my($this)=@_;

  $this->GetSet(FLAGS,join(",", $this->Flags()->Members()));
}

=item GetSet(I<field>,I<value>)

If I<value> is omitted,
return the current value of the I<field>.
If supplied,
set the value of the I<field>.

=cut

sub GetSet($$;$)
{ my($this,$field,$value)=@_;
  my $D = $this->_Data();

  return $D->{$field} if @_ <= 2;

  ##warn "GetSet: Set $field=$value";
  $D->{$field}=$value;
}

=back

=head1 SEE ALSO

cs::DBI(3), cs::DBI::Table::Hash(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
