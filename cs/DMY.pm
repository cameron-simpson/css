#!/usr/bin/perl
#
# Date related functions.
# Purer DMY version written 02dec2002 with some basis on the old
# cs::Date object.
#	- Cameron Simpson <cs@zip.com.au> 02dec2002
#

=head1 NAME

cs::DMY - convenience routines for date manipulation

=head1 SYNOPSIS

	use cs::DMY;

	$today = new cs::DMY();
	$then = new cs::DMY($yyyy,$mm,$dd);
	$then = new cs::DMY("yyyy-mm-dd");

=head1 DESCRIPTION

Assorted routines for maniplating, parsing and printing date information.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Time::Local;
use cs::Misc;
use cs::TM;

package cs::DMY;

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT ACCESS AND CREATION

=over 4

=item new cs::DMY()

Obtain a new object with today's localtime date.

=item new cs::DMY(I<yyyy>,I<mm>,I<dd>)

Obtain a new object with the specified date.

=item new cs::DMY(I<gmt>)

Obtain a new object from the specified GMT time,
rendered in local time.

=item new cs::DMY(I<yyyy-mm-dd>)

Obtain a new object from the specified ISO date,
rendered in local time.

=cut

sub new
{ my($class)=shift;

  my($dd,$mm,$year);

  if (! @_)
  # no arguments - today's date
  { ##warn "NEW(NOW)";
    return new cs::DMY(time);
  }

  if (@_ eq 3)
  { ($dd,$mm,$year)=@_;
  }
  elsif (@_ eq 1)
  { my $gmt=shift;
    if ($gmt =~ /^(\d{4})-0*(\d\d?)-0*(\d\d?)$/)
    # YYYY-MM-DD invocation
    { return new cs::DMY($3,$2,$1);
    }

    my $TM = new cs::TM($gmt);
    ($dd,$mm,$year)=($TM->MDay(), $TM->Mon+1, $TM->Year());
  }
  else
  { die "$0: bad arguments to new cs::DMY: [@_]";
  }

  bless [$dd,$mm,$year], $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Day()

Return the month day from this object (1..31).

=cut

sub Day { shift->[0]; }

=item Mon()

Return the month from this object (1..12).

=cut

sub Mon { shift->[1]; }

=item Year()

Return the year from this object.

=cut

sub Year { shift->[2]; }

=item Dmy()

Return an array containing (day,month,year).

=cut

sub Dmy()
{ my($this)=@_;
  @$this;
}

=item IsoDate

Return the ISO day code: YYYY-MM-DD.

=cut

sub IsoDate { my($this)=shift; sprintf("%04d-%02d-%02d",$this->[2],$this->[1],$this->[0]); }

=item Gmt()

Return the UNIX time in seconds of the start of this day,
using the local time zone.

=cut

sub Gmt
{
  my($d,$m,$y)=shift->Dmy();
  Time::Local::timelocal(0,0,0,$d,$m-1,$y-1900);
}

=item Prev(I<ndays>)

Return a new cs::DMY object I<ndays> earlier
(1 day earlier if not specified).

=cut

sub Prev
{ my($this,$n)=@_;
  $n=1 if ! defined $n;

  my($d,$m,$y)=$this->Dmy();

  while ($n > 0)
  { my $sub = ::min($n,$d-1);

    if ($sub > 0)
    { $n-=$sub;
      $d-=$sub;
    }
    else
    # got back one day across month boundary
    { my $day1 = new cs::DMY($d,$m,$y);
      my $gmt = $day1->Gmt();
      $gmt -= 18*3600;	# 18 hours earlier (1 day - 6 hours)
      my $day0 = new cs::DMY($gmt);
      ($d,$m,$y)=$day0->Dmy();
      $n--;
    }
  }

  return new cs::DMY($d,$m,$y);
}

=item Next(I<ndays>)

Return a new cs::DMY object I<ndays> later
(1 day later if not specified).

=cut

sub Next
{ my($this,$n)=@_;
  $n=1 if ! defined $n;

  my($d,$m,$y)=$this->Dmy();

  while ($n > 0)
  {
    if ($d < 28)
    { my $add = ::min(28-$d, $n);
      $n-=$add;
      $d+=$add;
    }
    else
    # got forward one day across month boundary
    {
      my $day1 = new cs::DMY($d,$m,$y);
      my $gmt = $day1->Gmt();
      $gmt += 30*3600;	# 30 hours later (1 day + 6 hours)
      my $day2 = new cs::DMY($gmt);
      ($d,$m,$y)=$day2->Dmy();
      $n--;
    }
  }

  return new cs::DMY($d,$m,$y);
}

=item Monday()

Return the first Monday not later than this date.

=cut

sub Monday
{ my($this)=@_;
  
  my $tm = new cs::TM($this->Gmt());
  my $wday = $tm->WDay();

  if ($wday == 0)
  { return $this->Prev(6);
  }
  if ($wday == 1)
  { return $this;
  }

  return $this->Prev($wday-1);
}

=back

=head1 SEE ALSO

cs::TM(3)

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au> 04dec2002

=cut

1;
