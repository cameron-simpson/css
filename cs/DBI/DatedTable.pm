#!/usr/bin/perl
#
# cs::DBI::DatedTable: control a table of attributes with a date range
#	- Cameron Simpson <cs@zip.com.au> 4jun2000
#

=head1 NAME

cs::DBI::DatedTable - control a table of attributes with a date range

=head1 SYNOPSIS

use cs::DBI::DatedTable;

=head1 DESCRIPTION

The cs::DBI::DatedTable module manipulates a table
of records with day-resolution
B<START_DATE> and B<END_DATE> fields
(the latter of which may be B<NULL> to indicate an open range).

=cut

use strict qw(vars);
use cs::Misc;
use cs::DBI;
use cs::Day;

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::DBI::DatedTable;

require Exporter;

@cs::DBI::DatedTable::ISA=qw();

=head1 OBJECT CREATION

=over 4

=item new cs::DBI::DatedTable I<dbh>,I<table>

Creates a new object attached to the specified database I<dbh> and I<table>.

=cut

sub new($$$)
{ my($class,$dbh,$table)=@_;

  bless { cs::DBI::DatedTable::DBH => $dbh,
	  cs::DBI::DatedTable::TABLE => $table,
	}, $class;
}

sub _Dbh() { shift->{cs::DBI::DatedTable::DBH}; }
sub _Table(){shift->{cs::DBI::DatedTable::TABLE}; }
sub _Array(){shift->{cs::DBI::DatedTable::ARRAY}; }

=back

=head1 OBJECT METHODS

=over 4

=item Find(I<when>,I<selwhere>)

Return an array of hashrefs
for all the records matching I<selwhere>
which overlap the day I<when>.
I<selwhere> is a flat array of I<field>/I<value> pairs
to select the records.

=cut

sub Find
{ my($this,$when,@selwhere)=@_;
  $when = isodate() if ! defined $when;

  my $dbh = $this->_Dbh();
  my $table = $this->_Table();

  my ($sql, @args) = cs::DBI::sqlWhereText("SELECT * FROM $table", @selwhere);
  $sql.=" WHERE" if ! @selwhere;
  $sql.=" AND START_DATE <= ? AND (ISNULL(END_DATE) OR END_DATE >= ?";

  my $sth = sql($dbh,$sql);
  return () if ! defined $sth;

  cs::DBI::fetchall_hashref($sth,@args,$when,$when);
}

=item AddRecord(I<rec>,I<when>)

Add the record denoted by the hashref I<rec>
to the table as a new record commencing I<when>.
The argument I<when> is optional and defaults to today.

=cut

sub AddRecord($$;$)
{ my($this,$rec,$when)=@_;
  $when = cs::DBI::isodate() if ! defined $when;

  $rec->{START_DATE}=$when;
  cs::DBI::insert($this->_Dbh(), $this->_Table(), keys %$rec)
	->ExecuteWithRec($rec);
}

=item DelRecord(I<when>,I<delwhere>)

Expire the record denoted by I<delwhere> at the date I<when>.
I<delwhere> is a flat array of I<field>/I<value> pairs
to select the record to expire.

=cut

sub DelRecord
{ my($this,$when,@delwhere)=@_;

  my $dbh = $this->_Dbh();
  my $table = $this->_Table();

  if (@delwhere < 2)
  { my(@c)=caller;
    die "$0: cs::DBI::DatedTable::DelRecord($table): no \@delwhere from [@c]";
  }

  # set closing date of the day before the deletion day
  my $today = new cs::Day $when;
  my $prevwhen = $today->Prev()->Code();

  my ($sql, @args)
   = cs::DBI::sqlWhereText("UPDATE $table SET END_DATE = ?", @delwhere);
  $sql .= " AND START_DATE <= ? AND ISNULL(END_DATE)";

  my $sth = sql($dbh, $sql);
  if (! defined $sth)
  { warn "$::cmd: cs::DBI::delDatedRecord($table): can't make sql to delete old records";
    return undef;
  }

  $sth->execute($prevwhen,@args,$when);
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
