#!/usr/bin/perl
#
# Yet another CDDB module.
# Core CD querying code adapted from FreeDB.pm with the
# discid function outsourced to a CDDB daemon (because both
# FreeDB.pm and CDDB_get.pm appear to have subtly broken
# algorithms).
#	- Cameron Simpson <cs@zip.com.au> 10sep2001
#

=head1 NAME

cs::CDDB - convenience routines for working with audio CDs and the CDDB and FreeDB databases

=head1 SYNOPSIS

use cs::CDDB;

=head1 DESCRIPTION

An assortment of routines for doing common things with audio CDs.

=cut

use strict qw(vars);
use cs::Misc;

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::CDDB;

@cs::CDDB::ISA=qw();

$cs::CDDB::VERSION='1.0';

=head1 GLOBAL VARIABLES

=over 4

=item $cs::CDDB::DfltDev

The default device to query for CD information.
Default: B</dev/cdrom>

=cut

$cs::CDDB::DfltDev='/dev/cdrom';

=head1 GENERAL FUNCTIONS

=over 4


=back

=head1 OBJECT CREATION

=over 4

=item new cs::CDDB I<host>, I<port>

Create a new B<cs::CDDB> object
attached to the CDDB daemon at the specified I<host> and I<port>.

=cut

sub new
{ my($class,$host,$port)=@_;
  if (! defined $host)
  { my $dflt = ( defined $ENV{CDDBSERVER} && length $ENV{CDDBSERVER}
	       ? $ENV{CDDBSERVER}
	       : "cddb:888"
	       );

    if ($dflt =~ /:/)
    { $port=$';
      $host=$`;
    }
    else
    { $port=888;
      $host=$dflt;
    }
  }
  elsif (! defined $port)
  { $port=888;
  }

  my $this
  = bless { DEV => $cs::CDDB::DfltDev,
	    HOST => $host,
	    PORT => $port,
	  }, $class;

  $this->Reset();

  return $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item Reset()

Forget everything learnt from the device or database.

=cut

sub Reset()
{ my($this)=@_;

  my $dev = $this->Device();

  for my $k (keys %$this)
  { delete $this->{$k}
	if $k ne DEV && $k ne HOST && $k ne PORT;
  }
}

=item Device(I<device>)

With no argument, returns the CD device for this object.
With an argument, sets the device to query and calls Reset().

=cut

sub Device($;$)
{ my($this,$dev)=@_;

  if (defined $dev)
  { $this->{DEV}=$this;
    $this->Reset();
  }

  return $this->{DEV};
}

=item Stat()

Query the device for information about the CD.
Returns success.

=cut

sub Stat($)
{ my($this)=@_;

  my $dev = $this->Device();

  $this->Reset();

  my $ok=0;

  if (! open(CDROM, "< $dev\0"))
  { warn "$::cmd: Stat($dev): open: $!\n";
    $ok
  }
  else
  {
    my $cdhdr = "";
    if (! ioctl(CDROM, 0x5305, $cdhdr))
    { warn "$::cmd: Stat($dev): ioctl(..,0x5305,..): $!\n";
    }
    else
    {
      $ok=1;

      my $tocentry;
      my @toc;

      my($start,$end)=unpack("CC", $cdhdr);

      my @tracks = ();
      my $oT;

      for my $tno ($start..$end)
      { $tocentry = pack('C8', $tno, 0, 2, 0, 0, 0, 0, 0);
	if (! ioctl(CDROM, 0x5306, $tocentry))
	{ warn "$::cmd: Stat($dev): ioctl(..,$0x5306,track=$tno): $!\n";
	  $ok=0;
	}
	else
	{ @toc=unpack("C*", $tocentry);
	  push(@tracks, { TRACK		=> $toc[0],
			  ADR_CTL	=> $toc[1],
			  FORMAT	=> $toc[2],
			  FRAME		=> $toc[3],
			  MINUTE	=> $toc[4],
			  SECONDS	=> $toc[5],
			  LENGTH	=> $toc[4]*60+$toc[5],
			});
	}
      }

      $tocentry = pack("C8", 0xAA, 0, 2, 0, 0, 0, 0, 0);
      if (! ioctl(CDROM,0x5306,$tocentry))
      { warn "$::cmd: Stat($dev): ioctl(..,0x5306, 0xAA...): $!\n";
	$ok=0;
      }
      else
      { 
	@toc=unpack("C*", $tocentry);
	push(@tracks, { TRACK		=> $toc[0],
			ADR_CTL		=> $toc[1],
			FORMAT		=> $toc[2],
			FRAME		=> $toc[3],
			MINUTE		=> $toc[4],
			SECONDS		=> $toc[5],
			LENGTH		=> $toc[4]*60+$toc[5],
		      });
      }

      if ($ok)
      {
	# tidy up records
	# make OFFSET = cumulative prelength * 75
	# make length = length - cumulative prelength
	for my $i (0..$#tracks-1)
	{ my $T = $tracks[$i];
	  my $nT= $tracks[$i+1];
	  $T->{OFFSET}=$T->{LENGTH}*75;
	  $T->{LENGTH}=$nT->{LENGTH}-$T->{LENGTH};
	}

	$tracks[$#tracks]->{OFFSET}=$tracks[$#tracks]->{LENGTH}*75;
	$tracks[$#tracks]->{LENGTH}=0;

	$this->{START}=$start;
	$this->{END}=$end;
	$this->{TRACKS}=[ @tracks ];
      }
    }

    close(CDROM);
  }

  return $ok;
}

sub _StatIf()
{ my($this)=@_;
  if (! exists $this->{TRACKS})
  { return $this->Stat();
  }
  1;
}

=item Tracks()

Return an array of track records.
Note that the last track is the lead-out, not audio.
Returns an empty array if there are problems obtaining information.

=cut

sub Tracks($)
{ my($this)=@_;

  $this->_StatIf() || return ();

  my(@t)=@{$this->{TRACKS}};
  pop(@t);

  return @t;
}

=item Length(I<track>)

If the track number I<track> is supplied,
return the play time of that track in seconds.
otherwise, return the total play time of the CD in seconds

=cut

sub Length($;$)
{ my($this,$tno)=@_;

  $this->_StatIf() || return undef;

  if (! defined $tno)
  { my $total=0;

    # total excluding lead-out
    for my $i (0..$#{$this->{TRACKS}}-1)
    { $total+=$this->{TRACKS}->[$i]->{LENGTH};
    }

    return $total;
  }

  for my $i (0..$#{$this->{TRACKS}}-1)
  { return $this->{TRACKS}->[$i]->{LENGTH}
	if $this->{TRACKS}->[$i]->{TRACK} == $tno;
  }

  # no match!
  return undef;
}

=item DiscId()

Return the CDDB disc id for this CD
as an integer
by consulting the CDDB daemon.

=cut

sub DiscId()
{ my($this)=@_;

  $this->_StatIf() || return undef;

  ::need(cs::Net::TCP);
  my $C;

  if (! defined ($C=cs::Net::TCP->new($this->{HOST},$this->{PORT})))
  { warn "$::cmd: connect(host=$this->{HOST},port=$this->{PORT}): $!\n";
    return undef;
  }

  local($_);
  my $discid;

  if (! defined($_=$C->GetLine()) || ! length)
  { warn "$::cmd: no welcome from CDDB daemon\n";
  }
  else
  {
    $C->Put("CDDB HELLO $ENV{USER} $ENV{HOSTNAME} $::cmd-cs::CDDB $cs::CDDB::VERSION\n");
    $C->Flush();
    if (! defined ($_=$C->GetLine()) || ! length)
    { warn "$::cmd: no response to CDDB HELLO\n";
    }
    elsif (! /^200/)
    { warn "$::cmd: bad response to CDDB HELLO\n\t$_";
    }
    else
    {
      my @t = $this->Tracks();
      if (@t)
      {
	@t = map($_->{OFFSET}, @t);
	my $qry = "DISCID ".scalar(@t)." @t ".$this->Length();

	## warn cs::Hier::h2a($this);
	## warn $qry;

	$C->Put("$qry\n");
	$C->Flush();
	if (! defined ($_=$C->GetLine()) || ! length)
	{ warn "$::cmd: no response to: $qry\n";
	}
	elsif (! /^200\s*disc\s*id\s*is\s*([\da-f]{8})/i)
	{ warn "$::cmd: bad response to: $qry\n\t$_";
	}
	else
	{ $discid=$1;
	}
      }
    }
  }

  return undef if ! defined $discid;
  return hex($discid);
}

=back

=head1 ENVIRONMENT

CDDBSERVER, the default CDDB daemon in the form B<I<host>:I<port>>

=head1 SEE ALSO

CDDB_get(3), FreeDB(3), cdtoc(1cs), cdsubmit(1cs)

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au> 10sep2001

=cut

1;
