#!/usr/bin/perl
#
# cs::Legato::Networker::Tape: a tape in Legato's Networker backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::Legato::Networker::Tape - a tape in Legato's Networker backup system

=head1 SYNOPSIS

use cs::Legato::Networker::Tape;

@labels = cs::Legato::Networker::tapes();

$tape   = cs::Legato::Networker::findTape($label);

$label = $tape->Label();
$usage = $tape->Usage();

=head1 DESCRIPTION

The B<cs::Legato::Networker::Tape> module
talks to the Legato Networker backup system,
permitting queries about tapes and jukeboxes.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Legato::Networker::Dump;

package cs::Legato::Networker::Tape;

require Exporter;

@cs::Legato::Networker::Tape::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=cut

$cs::Legato::Networker::Tape::_needTapeInfo=1;

sub _loadTapeInfo(;$)
{ my($force)=@_;
  $force=0 if ! defined $force;

  return 1 if ! $force && ! $cs::Legato::Networker::Tape::_needTapeInfo;
  $cs::Legato::Networker::Tape::_needTapeInfo=0;	# recursion block

  undef %cs::Legato::Networker::Tape::_tapeInfo;

  if (! open(MMINFO, "mminfo -a -v|"))
  { warn "$::cmd: can't pipe from mminfo: $!";
    return 0;
  }

  local($_);
  my($T,$D);

  $_=<MMINFO>;	# skip heading

  MMINFO:
  while (<MMINFO>)
  {
    chomp;

    #         label        client mmddyy              hhmmss             size          units   seq    flags  level  path
    print "[$_]\n";
    if (! m;^([a-z]\w+)\s+(\S+)\s+(\d\d/\d\d/\d\d)\s+(\d\d:\d\d:\d\d)\s+(\d+(\.\d*)?\s*\S+)\s+(\d+)\s(\S*)\s(\S*)\s(\S.*);i)
    { die "$::cmd: bad data from mminfo, line $.\n[$_]\n\t";
      next MMINFO;
    }

    my($label,$client,$mmddyy,$hhmmss,$size,$seq,$flags,$level,$path)
     =
      ($1,    $2,     $3,     $4,     $5,   $7,  $8,    $9,    $10);

    if (! defined ($T=find($label)))
    { $T = _new cs::Legato::Networker::Tape $label;
    }

    $D = _new cs::Legato::Networker::Dump ($seq,$client,$path,$flags,$level);
    push(@{$T->Dumps()}, $D->Seq());
  }

  if (! close(MMINFO))
  { warn "$::cmd: nonzero exit status from mminfo";
  }

  1;
}

=item tapes()

Return a list of the labels of all known tapes.

=cut

sub tapes()
{
  _loadTapeInfo();
  keys %cs::Legato::Networker::Tape::_tapeInfo;
}

=back

=head1 OBJECT CREATION

=over 4

=cut

sub _new($$)
{ my($class,$label)=@_;

  my $T = find($label);

  if (defined $T)
  { my @c=caller;
    die "$0: tape with label \"$label\" already exists\n\tfrom @c\n\t";
  }

  $T = $cs::Legato::Networker::Tape::_tapeInfo{$label}
     = { cs::Legato::Networker::Tape::LABEL => $label,
         cs::Legato::Networker::Tape::DUMPS => [],
       };

  bless $T, $class;
}

=item cs::Legato::Networker::Tape::find(I<label>)

Obtain a B<cs::Legato::Networker::Tape> object
representing the tape with the specified I<label>.

=cut

sub find($)
{ my($label)=@_;

  _loadTapeInfo();
  return undef if ! exists $cs::Legato::Networker::Tape::_tapeInfo{$label};

  $cs::Legato::Networker::Tape::_tapeInfo{$label};
}

=back

=head1 OBJECT METHODS

=over 4

=item Label()

Return the label for this tape.

=cut

sub Label($)
{ shift->{cs::Legato::Networker::Tape::LABEL};
}

=item Dumps()

Return an array ref of the dumps recorded for this tape.

=cut

sub Dumps()
{ shift->{cs::Legato::Networker::Tape::DUMPS};
}

=back

=head1 SEE ALSO

cs::Legato::Networker

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
