#!/usr/bin/perl
#
# cs::Palm::App::Address: a module for the Address Palm app
#	- Cameron Simpson <cs@zip.com.au> 17may2000
#

=head1 NAME

cs::Palm::App::Address - interface to a PDB file for the Palm Pilot Address application

=head1 SYNOPSIS

use cs::Palm::App::Address;

=head1 DESCRIPTION

The cs::Palm::App::Address module
accesses the database
for the Palm Pilot Address application.
It is a subclass of the B<cs::Palm::PDB> class.

While I had made some progress decoding an address book record,
I am greatly indebted to the documentation in p5-Palm-Address.pm
by Andrew Arensburger, which describes most of the format in detail.

=cut

use strict qw(vars);

use cs::Palm;
use cs::Palm::PDB;

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Palm::App::Address;

require Exporter;

@cs::Palm::App::Address::ISA=qw(cs::Palm::PDB);

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Palm::App::Address I<file>

Creates a new B<cs::Palm::App::Address> object attached to the specified I<file>.

=cut

sub new($$)
{ my($class,$file)=@_;

  my $this = new cs::Palm::PDB $file, 'addr', 'AddressDB';
  return undef if ! defined $this;

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Record(I<n>)

Return an object containing the content of the I<n>th record.

=cut

sub Record($$)
{ my($this,$nr)=@_;

  my $raw = $this->SUPER::Record($nr);
  return undef if ! defined $raw;

  my $R = {};
  my @f = unpack("CCCCCCCCC",$raw);
  $raw=substr($raw,9);

  my @s = ();
  while ($raw =~ /^([^\0]*)\0/)
  { my $s = $1;
    $raw=$';
    push(@s,$s);
  }

  $R->{F}=[@f];
  $R->{S}=[@s];

  bless $R, cs::Palm::App::Address;
}

=back

=head1 SEE ALSO

cs::Palm(3), cs::Palm::PDB(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
