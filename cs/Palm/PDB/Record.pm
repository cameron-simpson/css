#!/usr/bin/perl
#
# cs::Palm::PDB::Record: a Palm DataBase Record
#	- Cameron Simpson <cs@zip.com.au> 19may2000
#

=head1 NAME

cs::Palm::PDB::Record - a Oalm Pilot database record

=head1 SYNOPSIS

use cs::Palm::PDB::Record;

=head1 DESCRIPTION

The cs::Palm::PDB::Record module
represents a record from a Palm Pilot file.
Application specific subclasses

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Palm::PDB::Record;

require Exporter;

@cs::Palm::PDB::Record::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item thing(I<arg1>)

Blah.

=cut

sub thing($)
{ my(
}

=back

=head1 OBJECT CREATION

Preamble on creation methods.

=over 4

=item new cs::Palm::PDB::Record I<arg1>

Creates a new blah ...

=cut

sub new
{ my(,
}

=back

=head1 OBJECT METHODS

=over 4

=item $Record->Method1(I<arg1>...

Does thing ...

=cut

sub Method1($
{ my($this,
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
