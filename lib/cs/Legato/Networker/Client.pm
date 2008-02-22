#!/usr/bin/perl
#
# cs::Legato::Networker::Client: a client record in Legato's Networker backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::Legato::Networker::Client - a client record in Legato's Networker backup system

=head1 SYNOPSIS

use cs::Legato::Networker::Client;

=head1 DESCRIPTION

The B<cs::Legato::Networker::Client> module describes a client (host).

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Legato::Networker::Client;

require Exporter;

@cs::Legato::Networker::Client::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item clients()

Return a list of known clients.

=cut

sub clients()
{
  ::need(cs::Legato::Networker::Tape);
  cs::Legato::Networker::Tape::_loadTapeInfo();

  keys %cs::Legato::Networker::Client::_clientInfo;
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

  $C = $cs::Legato::Networker::Client::_clientInfo{$client}
     = { cs::Legato::Networker::Client::CLIENT => $client,
         cs::Legato::Networker::Client::DUMPS => [],
       };

  bless $C, $class;
}

=item cs::Legato::Networker::Client::find(I<client>)

Obtain a B<cs::Legato::Networker::Client> object
representing the named I<client>.

=cut

sub find($)
{ my($client)=@_;
  return undef if ! defined $cs::Legato::Networker::Client::_clientInfo{$client};
  $cs::Legato::Networker::Client::_clientInfo{$client};
}

sub get($)
{ my($client)=@_;

  my $C = find($client);
  $C = _new(cs::Legato::Networker::Client, $client) if ! defined $C;

  $C;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _AddDump($$)
{ my($this,$seq)=@_;
  push(@{$this->{cs::Legato::Networker::Client::DUMPS}}, $seq);
}

=item Dumps()

Return the sequence numbers of dumps for this clients.

=cut

sub Dumps($)
{ @{shift->{cs::Legato::Networker::Client::DUMPS}};
}

=item Dump(I<seq>)

Return the B<cs::Legato::Networker::Dump> object
for the specified sequence number I<seq>.

=cut

sub Dump($$)
{ my($this,$seq)=@_;
  ::need(cs::Legato::Networker::Dump);
  cs::Legato::Networker::Dump::find($seq);
}

=back

=head1 SEE ALSO

cs::Legato::Networker

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
