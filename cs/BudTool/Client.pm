#!/usr/bin/perl
#
# cs::BudTool::Client: a client record in the BudTool backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::BudTool::Client - a client record in the BudTool backup system

=head1 SYNOPSIS

use cs::BudTool::Client;

=head1 DESCRIPTION

The B<cs::BudTool::Client> module describes a client (host).

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::BudTool::Client;

require Exporter;

@cs::BudTool::Client::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item clients()

Return a list of known clients.

=cut

sub clients()
{
  ::need(cs::BudTool::Tape);
  cs::BudTool::Tape::_loadTapeInfo();

  keys %cs::BudTool::Client::_clientInfo;
}

=back

=head1 OBJECT CREATION

=over 4

=cut

sub _new($$)
{ my($class,$client)=@_;

  my $C = find($client);

  if (defined $C)
  { my @c=caller;
    die "$0: client with name \"$client\" already exists\n\tfrom @c\n\t";
  }

  $C = $cs::BudTool::Client::_clientInfo{$client}
     = { cs::BudTool::Client::CLIENT => $client,
         cs::BudTool::Client::DUMPS => [],
       };

  bless $C, $class;
}

=item cs::BudTool::Client::find(I<client>)

Obtain a B<cs::BudTool::Client> object
representing the named I<client>.

=cut

sub find($)
{ my($client)=@_;
  return undef if ! defined $cs::BudTool::Client::_clientInfo{$client};
  $cs::BudTool::Client::_clientInfo{$client};
}

sub get($)
{ my($client)=@_;

  my $C = find($client);
  $C = _new(cs::BudTool::Client, $client) if ! defined $C;

  $C;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _AddDump($$)
{ my($this,$seq)=@_;
  push(@{$this->{cs::BudTool::Client::DUMPS}}, $seq);
}

=item Dumps()

Return the sequence numbers of dumps for this clients.

=cut

sub Dumps($)
{ @{shift->{cs::BudTool::Client::DUMPS}};
}

=item Dump(I<seq>)

Return the B<cs::BudTool::Dump> object
for the specified sequence number I<seq>.

=cut

sub Dump($$)
{ my($this,$seq)=@_;
  ::need(cs::BudTool::Dump);
  cs::BudTool::Dump::find($seq);
}

=back

=head1 SEE ALSO

cs::BudTool

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
