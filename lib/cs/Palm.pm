#!/usr/bin/perl
#
# cs::Palm: general Palm Pilot stuff
#	- Cameron Simpson <cs@zip.com.au> 16may2000
#

=head1 NAME

cs::Palm - general Palm Pilot stuff

=head1 SYNOPSIS

use cs::Palm;

=head1 DESCRIPTION

The B<cs::Palm> module provides general facilities
for working with Palm Pilots.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Palm;

require Exporter;

@cs::Palm::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=cut

sub _offsetTZ()
{
  if (! defined $cs::Palm::_offsetTZ)
  { ::need(cs::Date);
    $cs::Palm::_offsetTZ=cs::Date::tzoffset();
  }

  $cs::Palm::_offsetTZ;
}

sub _offset1904()
{
  (17+66*365)*24*3600;	# 66 years + 17 leap days
}

=item gmt2palm(I<gmt>,I<no_tz>)

Convert a Perl timestamp
(UNIX time_t - seconds since 1-Jan-1970)
to a palm timestamp
(seconds since 1-Jan-1904).
If the optional parameter I<no_tz> is true,
suppress the implicit conversion to localtime.

=cut

sub gmt2palm($;$)
{ my($gmt,$notz)=@_;
  $notz=0 if ! defined $notz;

  $gmt += _offset1904();
  $gmt += _offsetTZ() if ! $notz;

  $gmt;
}

=item palm2gmt(I<palmdate>,I<no_tz>)

Convert a palm timestamp
(seconds since 1-Jan-1904)
into a Perl timestamp
(UNIX time_t - seconds since 1-Jan-1970).
If the optional parameter I<no_tz> is true,
suppress the implicit conversion from localtime.

=cut

sub palm2gmt($;$)
{ my($palm,$notz)=@_;
  $notz=0 if ! defined $notz;

  $palm -= _offset1904();
  $palm -= _offsetTZ() if ! $notz;

  $palm;
}

=item parseString0(I<data>)

Parse a NUL terminated string at the start I<data>.
Returns the string (without the NUL) in a scalar context
and an array of B<(I<string>,I<tail>)> in an array context
where I<tail> is the portion of I<data> after the NUL.
Returns B<undef> or an empty array on error.

=cut

sub parseString0($)
{ local($_)=@_;

  if (/^([^\0]*)\0/)
  { return $1 if ! wantarray;
    return ($1,$');
  }

  return () if wantarray;
  undef;
}

=back

=head1 OBJECT CREATION

=over 4

=item newPDB I<file>, I<creator>, I<dbname>

Return a new B<cs::Palm::PDB> object attached to I<file>
with the specified I<creator> and I<dbname> values.

=cut

sub newPDB
{ ::need(cs::Palm::PDB);

  cs::Palm::PDB->new(@_);
}

=item newAppDB I<app>, I<file>

Return a new I<app> specific object attached to I<file>,
a subclass of B<cs::Palm::PDB> named B<cs::Palm::App::I<app>>.

=cut

sub newAppDB($$)
{ my($app,$file)=@_;

  ::need("cs::Palm::App::$app");
  my $this = eval "cs::Palm::App::$app->new(\$file)";
  if ($@)
  { warn "$::cmd: eval: $@";
    return undef;
  }

  $this;
}

=back

=head1 SEE ALSO

cs::Date(3), cs::Palm::PDB(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
