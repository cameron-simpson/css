#!/usr/bin/perl
#
# cs::Legato::Networker::Dump: a dump record in Legato's Networker backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::Legato::Networker::Dump - a dump record in Legato's Networker backup system

=head1 SYNOPSIS

use cs::Legato::Networker::Dump;

=head1 DESCRIPTION

The B<cs::Legato::Networker::Dump> module
describes a dump record.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Legato::Networker::Tape;

package cs::Legato::Networker::Dump;

require Exporter;

@cs::Legato::Networker::Dump::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=cut

=back

=head1 OBJECT CREATION

=over 4

=cut

sub _new($$$$$$$)
{ my($class,$label,$seq,$client,$path,$flags,$level)=@_;

  my $D = find($label);

  if (defined $D)
  { my @c=caller;
    die "$0: dump with sequence number \"$seq\" already exists\n\tfrom @c\n\t";
  }

  $D = $cs::Legato::Networker::Dump::_dumpInfo[$seq]
     = { cs::Legato::Networker::Dump::TAPE => $label,
         cs::Legato::Networker::Dump::SEQ  => $seq,
         cs::Legato::Networker::Dump::CLIENT => $client,
         cs::Legato::Networker::Dump::PATH => $path,
         cs::Legato::Networker::Dump::FLAGS => $flags,
         cs::Legato::Networker::Dump::LEVEL => $level,
       };

  bless $D, $class;

  # cross reference
  ::need(cs::Legato::Networker::Client);
  cs::Legato::Networker::Client::get($client)->_AddDump($seq);

  $D;
}

=item cs::Legato::Networker::Dump::find(I<seq>)

Obtain a extant B<cs::Legato::Networker::Dump> object
representing the dump with sequence number I<seq>.

=cut

sub find($)
{ my($seq)=@_;
  return undef if ! defined $cs::Legato::Networker::Dump::_dumpInfo[$seq];
  $cs::Legato::Networker::Dump::_dumpInfo[$seq];
}

=back

=head1 OBJECT METHODS

=over 4

=item Seq()

Return the sequence number for this dump.

=cut

sub Seq($)
{ shift->{cs::Legato::Networker::Dump::SEQ};
}

=item TapeLabel()

Return the tape label for this dump.

=cut

sub TapeLabel($)
{ shift->{cs::Legato::Networker::Dump::TAPE};
}

=item Tape()

Return the B<cs::Legato::Networker::Tape> object for this dump.

=cut

sub Tape($)
{ ::need(cs::Legato::Networker::Tape);
  cs::Legato::Networker::Tape::find(shift->TapeLabel());
}

=item ClientName()

Return the client name for this dump.

=cut

sub ClientName($)
{ shift->{cs::Legato::Networker::Dump::CLIENT};
}

=item Client()

Return the B<cs::Legato::Networker::Client> object for this dump.

=cut

sub Client($)
{ ::need(cs::Legato::Networker::Client);
  cs::Legato::Networker::Client::find(shift->ClientName());
}

=back

=head1 SEE ALSO

cs::Legato::Networker

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
