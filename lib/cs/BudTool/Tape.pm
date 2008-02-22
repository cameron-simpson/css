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

  if (! open(VOLLS, "rootenv /usr/budtool/bin/btvolls -v|"))
  { warn "$::cmd: can't pipe from btvolls: $!";
    return 0;
  }

  # tape info
  my $T;
  my($label,$loc,$subloc,$expiry,$full,$entrydate,$owrites,$errtype);

  # dump info
  my $D;
  my($dumpid,$fno,$size,$user,$id,$class,$date,$host);

  my $state;
  my @match;

  VOLLS:
  while (<VOLLS>)
  {
    chomp;
    s/\s+$//;
    next VOLLS if ! length;

    if (/^-volume-label/)
    # heading: new tape info
    { last VOLLS if ! defined ($_=<VOLLS>);
      chomp;
      s/\s+$//;

      if (! /^\s+-\s+fno/)
      { die "$::cmd: btvolls -v, line $.: expected secondary header line, got:\n\t$_\n";
      }

      # read tape label and core info
      HDR1:
      while (defined ($_=<VOLLS>))
      { chomp;
	s/\s+$//;
	last HDR1 if length;
      }
      die "$::cmd: btvolls: EOF looking for tape label line\n" if ! defined;

      #                  -volume-label----------------- -location------ -sublocation--- -expire-date- -full?- -entry-date-- -overwrites- -error-type-----------
      die "$::cmd: btvolls, line $.: bad label data:\n\t$_\n"
	if ! (@match = /^(.{30}) (.{5}) (.{15}) (.{13}) (.{7}) (.{13}) (.{12}) (.*)/);

      # tidy data fields
      for (@match)
      { s/^\s+//;
	s/\s+$//;
	$_="" if $_ eq 'N/A';
      }

      ($label,$loc,$subloc,$expiry,$full,$entrydate,$owrites,$errtype)=@match;

      ::out("$label");

      $T=get($label);
      $T->Location($loc) if length $loc;
      $T->SubLocation($subloc) if length $subloc;
      $T->Expiry(cs::BudTool::mdyyyy2gmt($expiry)) if length $expiry;
      $T->Full(cs::BudTool::yesno($full)) if length $full;
      $T->EntryDate(cs::BudTool::mdyyyy2gmt($entrydate)) if length $entrydate;
      $T->OverWrites($owrites+0) if length $owrites;
      $T->ErrorType($errtype) if length $errtype;

      $state=INTAPE;
    }
    elsif ($state ne INTAPE)
    { warn "$::cmd: btvolls, line $.: unexpected data:\n\t$_\n";
    }
    elsif (! (@match =~ /^(.{12}) (.{10}) (.{10}) (.{20}) (.{25}) (.{10}) (.*)/))
    { die "$::cmd: btvolls, line $.: bad dump line:\n\t$_\n";
    }
    else
    {
      # tidy data fields
      for (@match)
      { s/^\s+//;
	s/\s+$//;
	$_="" if $_ eq 'N/A';
      }

      ($fno,$size,$user,$id,$class,$date,$host)=@match;

      $dumpid = "$label:$fno";
      ::out($dumpid);

      $D = cs::BudTool::Dump::find($dumpid);
      if (defined $D)
      { warn "$::cmd: btvolls, line $.: rejecting multiple entries for dump \"$dumpid\":\n\t$_\n";
      }
      else
      {
        $D=_new cs::BudTool::Dump($dumpid);
	$T->AddDump($dumpid);
	$D->AddTape($label);

	$D->Label($label);
	$D->FileNo($fno);
	$D->Size(cs::BudTool::nu2size($size)) if length $size;
	$D->User($user) if length $user;
	$D->SetId($id) if length $id;
	$D->Class(cs::BudTool::classlevel2class($class)) if length $class;
	$D->Level(cs::BudTool::classlevel2level($class)) if length $class;
	$D->Date(cs::BudTool::mdyyyy2gmt($date)) if length $date;
	$D->Host(cs::BudTool::hostpath2host($host)) if length $host;
	$D->Path(cs::BudTool::hostpath2path($host)) if length $host;
      }
    }
  }

  ::out('');

  if (! close(VOLLS))
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

cs::BudTool(3), btvolls(1)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
