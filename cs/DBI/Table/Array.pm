#!/usr/bin/perl
#
# cs::DBI::Table::Array: Treat a DBI table as an array.
#	- Cameron Simpson <cs@zip.com.au> 01jun2000
#

=head1 NAME

cs::DBI::Table::Array - treat a DBI table as an array

=head1 SYNOPSIS

use cs::DBI::Table::Array;

tie @a, cs::DBI::Table::Array, I<dbh>, I<table>, I<where>;

use cs::DBI;

$arrayref=cs::DBI::arraytable(I<dbh>,I<table>,I<where>,I<preload>)

=head1 DESCRIPTION

The cs::DBI::Table::Array module permits you to tie a database
table to a hash.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::DBI;

package cs::DBI::Table::Array;

require Exporter;

@cs::DBI::Table::Array::ISA=qw();

=head1 OBJECT ATTACHMENT

=over 4

=item tie I<array>, B<cs::DBI::Table::Array>, I<dbh>, I<table>, I<where>

Attach I<array> to the I<table> in database I<dbh>.
I<where> must be an SQL select...where condition
to constrain the records to which I<hash> now applies,
or the empty string.

=cut

sub TIEARRAY($$$$$)
{ my($class,$dbh,$table,$where)=@_;

  my $this =
  bless { DBH => $dbh,
	  TABLE => $table,
	  WHERE => $where,
	  ROWS => [],
	}, $class;


  my $sql = "SELECT * FROM $this->{TABLE}";
  $sql.=" WHERE $this->{WHERE}" if length $this->{WHERE};
  my $sth = cs::DBI::sql($dbh, $sql);
  $this->{LIVE}=[ cs::DBI::fetchall_hashref($sth) ];

  ## warn "after TIEARRAY, LIVE=".cs::Hier::h2a($this->{LIVE},1);

  $this;
}

sub FETCHSIZE($)
{ my($this)=@_;
  scalar(@{$this->{LIVE}});
}

sub FETCH($$)
{ my($this,$ndx)=@_;

  my $live = $this->{LIVE};
  return undef if $ndx < 0 || $ndx > $#$live;

  $live->[$ndx];
}

# expect key to take on last_id if undef
sub STORE($$$)
{ my($this,$ndx,$value)=@_;

  my@c=caller;
  warn "$::cmd: STORE unsupported on DBI table\n\tfrom [@c]\n\tndx=$ndx,value=$value\n\t";
}

sub DELETE($$)
{ my($this,$ndx)=@_;

  my@c=caller;
  warn "$::cmd: DELETE unsupported on DBI table\n\tfrom [@c]\n\tndx=$ndx\n\t";
}

sub PUSH($$)
{ my($this,$value)=@_;

  push(@{$this->{LIVE}},$value);
  cs::DBI::insert($this->{DBH},$this->{TABLE}, keys %$value)
	->ExecuteWithRec($value);
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.au<gt>

=cut

1;
