#!/usr/bin/perl
#
# cs::Legato::Networker: a module for working with Legato's Networker backup system.
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::Legato::Networker - a module for working with Legato's Networker backup system

=head1 SYNOPSIS

use cs::Legato::Networker;

@labels = cs::Legato::Networker::tapes();

$tape   = cs::Legato::Networker::findTape($label);

=head1 DESCRIPTION

The B<cs::Legato::Networker> module
talks to the Legato Networker backup system,
permitting queries about tapes and jukeboxes.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Legato::Networker::Tape;

package cs::Legato::Networker;

require Exporter;

@cs::Legato::Networker::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item tapes()

Return a list of the labels of all known tapes.

=cut

sub tapes()
{ &cs::Legato::Networker::Tape::tapes;
}

=back

=head1 OBJECT CREATION

=over 4

=cut

=back

=head1 OBJECT METHODS

=over 4

=cut

=back

=head1 SEE ALSO

cs::Legato::Networker::Tape

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
