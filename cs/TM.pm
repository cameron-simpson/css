#!/usr/bin/perl
#
# Trivial wrapper for the struct-tm array returned by localtime() and gmtime().
#	- Cameron Simpson <cs@zip.com.au> 03dec2002
#

=head1 NAME

cs::TM - convenience routines struct-tm manipulation

=head1 SYNOPSIS

	use cs::TM;

=head1 DESCRIPTION

Trivial wrapper for the struct-tm array returned by localtime() and gmtime().

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Time::Local;
use cs::Misc;

package cs::TM;

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT ACCESS AND CREATION

=over 4

=item new cs::TM()

Obtain a new object with today's localtime data.

=item new cs::TM(I<gmt>)

Obtain a new object from the specified GMT time,
rendered in local time.

=cut

sub new
{ my($class)=shift;

  my @tm;

  if (! @_)
  # no arguments - today's date
  { @tm=localtime(time);
  }
  elsif (@_ eq 1)
  { @tm=localtime($_[0]);
  }
  else
  { die "$0: bad call to new cs::TM: args are [@_]";
  }

  bless \@tm, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Sec(I<emitlocaltime>), Min(I<emitlocaltime>), Hour(I<emitlocaltime>), MDay(I<emitlocaltime>), Mon(I<emitlocaltime>), WDay(I<emitlocaltime>), YDay(I<emitlocaltime>), Year(I<emitlocaltime>), YY(I<emitlocaltime>)

Return this cs::TM's seconds, minutes, hours, day of month (1..31),
month (1..12), day of week (Sun=0,.., Sat=6), day of year (0..365),
year, short year (year-1900), 2-digit year,
whether daylight saving (aka summer) time is in effect
from this date's TM hash, respectively.

=cut

sub Sec { return shift->[0]; }
sub Min { return shift->[1]; }
sub Hour{ return shift->[2]; }
sub MDay{ return shift->[3]; }
sub Mon { return shift->[4]; }
sub WDay{ return shift->[6]; }
sub YDay{ return shift->[7]; }
sub Year1900 { return shift->[5]; }
sub Year { return shift->[5]+1900; }
sub Yy { return shift->[5]%100; }
sub IsDst{ return shift->[8]; }

=back

=head1 SEE ALSO

cs::DMY(), perlfunc(1)

=head1 AUTHOR

Cameron Simpson &lt;cs@zip.com.au&gt; 04dec2002

=cut

1;
