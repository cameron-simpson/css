#!/usr/bin/perl
#
# cs::BudTool: a module for working with the BudTool backup system.
#	- Cameron Simpson <cs@zip.com.au> 07nov2000
#

=head1 NAME

cs::BudTool - a module for working with the BudTool backup system

=head1 SYNOPSIS

use cs::BudTool;

@labels = cs::BudTool::tapes();

$tape   = cs::BudTool::findTape($label);

=head1 DESCRIPTION

The B<cs::BudTool> module
talks to the BudTool backup system,
permitting queries about tapes and jukeboxes.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::BudTool::Tape;
use cs::BudTool::Dump;
use cs::BudTool::Client;

package cs::BudTool;

require Exporter;

@cs::BudTool::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item tapes()

Return a list of the labels of all known tapes.

=cut

sub tapes()
{ &cs::BudTool::Tape::tapes;
}

=item clients()

Return a list of the client names known.

=cut

sub clients()
{ &cs::BudTool::Client::clients;
}

=back

=head1 OBJECT CREATION

=over 4

=item tape(I<label>)

Return the B<cs::BudTool::Tape> object with the specified I<label>
or B<undef> if none.

=cut

sub tape($)
{ &cs::BudTool::Tape::find;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

=back

=head1 SEE ALSO

cs::BudTool::Tape

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
