#!/usr/bin/perl
#
# cs::BudTool::Tape: a tape in the BudTool backup system
#	- Cameron Simpson <cs@zip.com.au> 11oct2000
#

=head1 NAME

cs::BudTool::Tape - a tape in the BudTool backup system

=head1 SYNOPSIS

use cs::BudTool::Tape;

@labels = cs::BudTool::tapes();

$tape   = cs::BudTool::find($label);

$label = $tape->Label();
$usage = $tape->Used();

=head1 DESCRIPTION

The B<cs::BudTool::Tape> module
accesses the BudTool tape database.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Object;
use cs::BudTool::Dump;

package cs::BudTool::Tape;

require Exporter;

@cs::BudTool::Tape::ISA=qw(cs::Object);

=head1 GENERAL FUNCTIONS

=over 4

=cut

$cs::BudTool::Tape::_needTapeInfo=1;

sub _loadTapeInfo(;$)
{ my($force)=@_;
  $force=0 if ! defined $force;

  return 1 if ! $force && ! $cs::BudTool::Tape::_needTapeInfo;
  $cs::BudTool::Tape::_needTapeInfo=0;	# recursion block

  undef %cs::BudTool::Tape::_tapeInfo;

  local($_);
  my($T,$D);

  if (! open(MMINFO, "mminfo -a -r 'volume(15),\%used(5),pool(15),ssid(11),client(64),attrs(31),level(9),location(15),volume'|"))
  { warn "$::cmd: can't pipe from mminfo: $!";
    return 0;
  }

  # mminfo takes a while - get jukebox info while waiting
  if (! open(JUKE,"nsrjb -C|"))
  { warn "$::cmd: can't pipe from nsrjb: $!";
    return 0;
  }

  while (<JUKE>)
  {
    chomp;

    #        slot   label
    if (/^\s*(\d+): (.{13})/)
    {
      my($slot,$label)=($1,$2);
      $label =~ s/\s+$//;
      $label =~ s/\*$//;

      if ($label =~ /^\w/)
      { if ($label !~ /^[A-Z][A-Z][A-Z]\d\d\d$/)
	{ die "nsrjb: bad label \"$label\"\n\t$_\n";
	}
	else
	{ ## warn "label=$label, slot=$slot\n";
	  $T=get($label);
	  $T->Slot($slot);
	}
      }
    }
    elsif (/^drive\s+(\d+).*slot (\d+):\s*(\S+)/)
    { my($drive,$slot,$label)=($1,$2,$3);
      $T=find($label);
      if (! defined $T || $T->Slot() ne $slot)
      { warn "$::cmd: bad data from nsrjb -C: $_\n";
      }
      else
      { $T->Drive($drive);
      }
    }
  }

  close(JUKE);

  # ok, now parse the mminfo data
  $_=<MMINFO>;	# skip heading

  my @match;

  MMINFO:
  while (<MMINFO>)
  {
    chomp;

    #                 label  used  pool   ssid   client attrs  level location
    if (! (@match = /^(.{15})(.{5})(.{15})(.{11})(.{64})(.{31})(.{9})(.{15})/))
    { die "$::cmd: bad data from mminfo, line $.\n[$_]\n\t";
      next MMINFO;
    }

    for (@match)
    { s/^\s+//;
      s/\s+$//;
    }

    ## warn "match=[".join('|',@match)."]\n";

    my($label,$used,$pool,$ssid,$client,$attrs,$level,$location)
     =@match;

    ::out("$label: $ssid");
    ## warn "$label: used=$used, ssid=$ssid";

    $T=get($label);
    $T->Pool($pool);
    $T->Used($used);
    die "$label: no used field\n\t[$_]\n\t" if ! defined $used;
    $T->Location($location);

    $T->AddDump($ssid);

    if (! defined ($D=cs::BudTool::Dump::find($ssid)))
    { $D = _new cs::BudTool::Dump ($label,$ssid,$level,$client,$attrs);
    }
    else
    { $D->AddTape($label);
    }
  }
  ::out('');

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
  keys %cs::BudTool::Tape::_tapeInfo;
}

=back

=head1 OBJECT ACCESS

=over 4

=cut

sub _new($$)
{ my($class,$label)=@_;

  my $T = find($label);

  if (defined $T)
  { my @c=caller;
    die "$0: tape with label \"$label\" already exists\n\tfrom @c\n\t";
  }

  $T = $cs::BudTool::Tape::_tapeInfo{$label}
     = { cs::BudTool::Tape::LABEL => $label,
         cs::BudTool::Tape::DUMPS => [],
       };

  bless $T, $class;
}

=item find(I<label>)

Obtain a B<cs::BudTool::Tape> object
representing the tape with the specified I<label>.
Return B<undef> if the I<label> is not known.

=cut

sub find($)
{ my($label)=@_;

  _loadTapeInfo();
  return undef if ! exists $cs::BudTool::Tape::_tapeInfo{$label};

  $cs::BudTool::Tape::_tapeInfo{$label};
}

=item get(I<label>)

Obtain a B<cs::BudTool::Tape> object
representing the tape with the specified I<label>.
Creates a new object if the I<label> is unknown.

=cut

sub get($)
{ my($label)=@_;

  my $T = find($label);

  $T = _new cs::BudTool::Tape $label if ! defined $T;

  $T;
}

=back

=head1 OBJECT METHODS

=over 4

=item Label()

Return the label for this tape.

=cut

sub Label($)
{ shift->{cs::BudTool::Tape::LABEL};
}

=item Dumps()

Return an array ref of the dumps recorded for this tape.

=cut

sub Dumps()
{ shift->{cs::BudTool::Tape::DUMPS};
}

=item AddDump(I<ssid>)

Add the dump with save set id I<ssid>
the the list of dumps on this tape.

=cut

sub AddDump($$)
{ my($this,$ssid)=@_;

  push(@{$this->Dumps()}, $ssid);
}

=item Pool(I<pool>)

Get or set the I<pool> for this tape.

=cut

sub Pool($;$)
{ my($this)=shift;
  $this->GetSet(cs::BudTool::Tape::POOL,@_);
}

=item Used(I<used>)

Get or set the I<used> value for this tape.

=cut

sub Used($;$)
{ my($this)=shift;
  ## warn "Used($this,@_)";
  $this->GetSet(cs::BudTool::Tape::USED,@_);
}

=item Location(I<location>)

Get or set the I<location> value for this tape.

=cut

sub Location($;$)
{ my($this)=shift;
  $this->GetSet(cs::BudTool::Tape::LOCATION,@_);
}

=item Slot(I<slot>)

Get or set the I<slot> value for this tape.

=cut

sub Slot($;$)
{ my($this)=shift;
  $this->GetSet(cs::BudTool::Tape::SLOT,@_);
}

=item Drive(I<drive>)

Get or set the I<drive> value for this tape.

=cut

sub Drive($;$)
{ my($this)=shift;
  $this->GetSet(cs::BudTool::Tape::DRIVE,@_);
}

=back

=head1 SEE ALSO

cs::BudTool

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
