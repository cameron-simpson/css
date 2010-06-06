#!/usr/bin/perl
#
# Some simple units conversions.
#	- Cameron Simpson <cs@zip.com.au> 17jan2002
#

=head1 NAME

cs::Units - simple units conversions

=head1 SYNOPSIS

use cs::Units;

$elapsed = cs::Units::sec2human(3);

$storage = cs::Units::bytes2human();

$count = cs::Units::num2human(2);

@dec = cs::Units::decompose($somevalue, \@unitsTbale, $count);

=head1 DESCRIPTION

This module implements a simple decomposition of numbers into human friendly subunits.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Units;

@cs::Units::TimeUnits = ( 's', 1,		# second
			  'm', 60,		# minute
			  'h', 3600,		# hour
			  'd', 86400,		# day
			  'w', 604800,		# week
			  'M', 2592000,		# month (30 days)
			  'Y', 31556926,	# year (365.2422 days)
			);

@cs::Units::StorageUnits=('b', 1,		# byte
			  'k', 1024,		# binary kilobyte
			  'M', 1048576,		# binary megabyte
			  'G', 1073741824,	# binary gigabyte
			  'T', 1099511627776,	# binary terabyte
			  'P', 1125899906842624,# binary petabyte
			 );

@cs::Units::SizeUnits = ( 'u', 1,		# unit
			  'k', 1000,		# kilo
			  'M', 1000000,		# mega
			  'G', 1000000000,	# giga
			  'T', 1000000000000,	# tera
			  'P', 1000000000000000,# peta
			);

=head1 GENERAL FUNCTIONS

=over 4

=item decompose(I<value>,I<units>,I<count>)

Return the decomposition of the supplied I<value> into subunits
based on the table supplied in the arrayref I<units>
using at most I<count> subdivisions (default 2).
In an array context returns an array of (I<n1>,I<ab1>, I<n2>,I<ab2>, ...)
where I<n1> is the number of units of size 1 with abbrevation I<ab1>
and so forth, from largest units to smallest.
In a scalar context returns the concatenation of this array.

=cut

sub decompose($$;$)
{ my($num,$units,$count)=@_;
  $count=2 if ! defined $count;

  my @u = @$units;
  my @subu = ();

  my($size,$ab);

  # compile the units table
  COMP:
  while (@u)
  { ($ab,$size)=(shift(@u), shift(@u));
    if ($size <= $num)
    { push(@subu,$ab,$size);
    }
    else
    { last COMP;
    }
  }

  # always at least 1 item
  if (! @subu)
  { push(@subu,shift(@u),shift(@u));
  }

  my @dec = ();
  my $n1;

  # decompose the value
  while ($count > 0 && @subu)
  {
    ($size,$ab)=(pop(@subu),pop(@subu));

    $n1 = int($num/$size);
    if ($n1 < 10 && $count == 1) # get u.d if only u (i.e. 1/10th precision)
    { $n1 = int($num*10/$size)/10;
    }
    push(@dec, $n1, $ab);
    $num-=$n1*$size;

    $count--;
  }

  return wantarray ? @dec : join('',@dec);
}

=item sec2human(I<seconds>,I<count>)

Convenience routine calling decompose(I<secs>,\@cs::Units::TimeUnits,I<count>).

=cut

sub sec2human($;$)
{ my($sec,$count)=@_;
  $count=2 if ! defined $count;

  return decompose($sec,\@cs::Units::TimeUnits,$count);
}

=item bytes2human(I<bytes>,I<count>)

Convenience routine calling decompose(I<bytes>,\@cs::Units::StorageUnits,I<count>).

=cut

sub bytes2human($;$)
{ my($bytes,$count)=@_;
  $count=2 if ! defined $count;

  return decompose($bytes,\@cs::Units::StorageUnits,$count);
}

=item num2human(I<number>,I<count>)

Convenience routine calling decompose(I<number>,\@cs::Units::SizeUnits,I<count>).

=cut

sub num2human($;$)
{ my($um,$count)=@_;
  $count=2 if ! defined $count;

  return decompose($um,\@cs::Units::SizeUnits,$count);
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
