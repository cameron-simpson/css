#!/usr/bin/perl
#
# cs::BudTool::Dump: a dump record in the BudTool backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::BudTool::Dump - a dump record in the BudTool backup system

=head1 SYNOPSIS

use cs::BudTool::Dump;

=head1 DESCRIPTION

The B<cs::BudTool::Dump> module
describes a dump record.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::BudTool::Tape;

package cs::BudTool::Dump;

require Exporter;

@cs::BudTool::Dump::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT CREATION

=over 4

=cut

sub _new($$$$$$$)
{ my($class,$label,$ssid,$level,$client,$attrs)=@_;

  my $D = find($ssid);

  if (defined $D)
  { my @c=caller;
    die "$0: dump with save set number \"$ssid\" already exists\n\tfrom @c\n\t";
  }

  $D = $cs::BudTool::Dump::_dumpInfo[$ssid]
     = { cs::BudTool::Dump::TAPES	=> [],
         cs::BudTool::Dump::SSID	=> $ssid,
         cs::BudTool::Dump::CLIENT	=> $client,
         cs::BudTool::Dump::ATTRS	=> $attrs,
         cs::BudTool::Dump::LEVEL	=> $level,
       };

  bless $D, $class;

  # cross reference
  ::need(cs::BudTool::Client);
  cs::BudTool::Client::get($client)->_AddDump($ssid);

  $D->AddTape($label);

  $D;
}

=item cs::BudTool::Dump::find(I<ssid>)

Obtain a extant B<cs::BudTool::Dump> object
representing the dump with save set number I<ssid>.

=cut

sub find($)
{ my($ssid)=@_;

  ## my@c=caller;die"Dump::find(ssid=$ssid)\n\tfrom [@c]\n\t";

  return undef if ! defined $cs::BudTool::Dump::_dumpInfo[$ssid];
  $cs::BudTool::Dump::_dumpInfo[$ssid];
}

=back

=head1 OBJECT METHODS

=over 4

=item SSid()

Return the save set number for this dump.

=cut

sub SSid($)
{ shift->{cs::BudTool::Dump::SSID};
}

sub _Tapes($)
{ shift->{cs::BudTool::Dump::TAPES};
}

=item TapeLabels()

Return the tape labels for this dump.

=cut

sub TapeLabels($)
{ @{shift->_Tapes()};
}

=item AddTape(I<label>)

Add the tape with the specified I<label>
the the list of tapes spanned by this dump.

=cut

sub AddTape($$)
{ my($this,$label)=@_;

  push(@{$this->_Tapes()}, $label);
}

=item ClientName()

Return the client name for this dump.

=cut

sub ClientName($)
{ shift->{cs::BudTool::Dump::CLIENT};
}

=item Client()

Return the B<cs::BudTool::Client> object for this dump.

=cut

sub Client($)
{ ::need(cs::BudTool::Client);
  cs::BudTool::Client::find(shift->ClientName());
}

=back

=head1 SEE ALSO

cs::BudTool

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
