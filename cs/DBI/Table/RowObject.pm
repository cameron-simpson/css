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

=item new cs::DBI::Table::RowObject $rowhashref

Return an object attached to the table specified.
Note: multiple calls to B<new> with the same arguments
obtain multiple references to the same B<cs::DBI::hashtable>
(though you shou;dn't consider this guarenteed
- this is more a warning that B<new> doesn't always make a new object).

=cut

sub new($$)
{ my($class,$rowhash)=@_;

  bless { cs::DBI::Table::RowObject::DATA => $rowhash }, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _Data { shift->{cs::DBI::Table::RowObject::DATA}; }

=item GetSet(I<field>,I<value>)

If I<value> is omitted,
return the current value of the I<field>.
If supplied,
set the value of the I<field>.

=cut

sub GetSet($$;$)
{ my($this,$field,$value)=@_;
  my $D = $this->_Data();
  return $D->{$field} if ! defined $value;
  warn "GetSet: Set $field=$value";
  $D->{$field}=$value;
}

=back

=head1 SEE ALSO

cs::DBI(3), cs::DBI::Table::Hash(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
