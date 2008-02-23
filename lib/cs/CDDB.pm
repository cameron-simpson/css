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
use cs::Pathname;

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::CDDB;

@cs::CDDB::ISA=qw();

$cs::CDDB::VERSION='1.0';

$cs::CDDB::CacheDir="$ENV{HOME}/.cddb";

=head1 GLOBAL VARIABLES

=over 4

=item $cs::CDDB::DfltDev

The default device to query for CD information.
Default: the envvar B<$CDDBDEVICE> or B</dev/cdrom>

=cut

$cs::CDDB::DfltDev=(length($ENV{CDDBDEVICE}) ? $ENV{CDDBDEVICE} : '/dev/cdrom');

=back

=head1 GENERAL FUNCTIONS

=over 4

=item conn(I<host:port>)

Obtain a B<cs::Port> attached to the CDDB server at the specified I<host> and I<port>.
If these are omitted, use the environment variable B<$CDDBSERVER>.
Returns and array of B<(I<conn>,I<welcome>)>
being the connection and the welcome message respectively,
or an empty array on error.

=cut

sub conn(;$)
{ my($hostport)=@_;
  $hostport=$ENV{CDDBSERVER} if ! defined $hostport;

  if ($hostport !~ /^([^:]+):(\d+)/)
  { warn "$::cmd: cs::CDDB::conn: bad host:port pair \"$hostport\"\n";
    return ();
  }

  ::need(cs::Net::TCP);
  my $C = cs::Net::TCP->new($1,$2);

  return () if ! defined $C;

  my $hi = $C->GetLine();
  return () if ! defined $hi;
  chomp($hi);
  $hi =~ s/\r$//;
  warn "<- $hi\n";
  if ($hi !~ /^20[01]/)
  { warn "$::cmd: cs::CDDB::conn: unwelcome greeting from server: $hi\n";
    return ();
  }

  my($code,$etc)=command($C,"CDDB HELLO $ENV{USER} $ENV{HOSTNAME} $::cmd-cs::CDDB $cs::CDDB::VERSION");
  if (! defined $code)
  { warn "$::cmd: cs::CDDB::conn: no response to CDDB HELLO\n";
    return ();
  }
  elsif ($code !~ /^200/)
  { warn "$::cmd: bad response to CDDB HELLO: $code $etc\n";
    return ();
  }

  return ($C,$hi);
}

=item command(I<conn>,I<command>,I<upload>)

Send the specified CDDB I<command> to the server available on I<conn>,
a B<cs::Port> typically obtained with B<cs::Net::TCP-E<gt>new>.
For commands accompanied by data,
the lines specified in the array I<upload>
will be sent on receipt of a suitable B<32x> response to the initial command.
Returns an array of B<(I<code>,I<etc>,@<additional>)>.

=cut

sub command
{ my($C,$command,@upload)=@_;
  if (@upload == 1 && ref $upload[0])
  { @upload=@{$upload[0]};
  }

  warn "-> $command\n";
  $C->Put("$command\n");
  $C->Flush();

  local($_);
  $_=$C->GetLine();
  return () if ! defined || ! length;
  chomp;
  s/\r$//;
  warn "<- $_\n";
  if (! /^(\d)(\d)(\d)\s*/)
  { warn "$::cmd: cs::CDDB::command(..,\"$command\"): bad response: $_\n";
    return ();
  }

  my($code,$etc,$mid)=("$1$2$3",$',$2);
  my(@additional);

  if ($mid eq 1)
  { ADDITIONAL:
    while (defined($_=$C->GetLine()) && length)
    { chomp;
      s/\r$//;
      warn "<- $_\n";
      last ADDITIONAL if $_ eq ".";
      push(@additional,$_);
    }
  }
  elsif ($mid eq 2)
  { for my $up (@upload)
    { $C->Put($up, "\n");
    }
    $C->Put(".\n");
    $C->Flush();
  }

  return ($code,$etc,@additional);
}

=item cachefile(I<category>,I<discid>)

Return the pathname of the corresponding B<~/.cddb> cache file.

=cut

sub cachefile($$)
{ "$cs::CDDB::CacheDir/$_[0]/$_[1]";
}

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
  { $this->{DEV}=$dev;
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
    my $cdhdr = ' ' x 256;
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
Returns an empty array if there are problems obtaining information.

Note that the last track is the lead-out, not audio,
and that this is only the data directly determinable
from the disc.
For track names etcetera you should use B<TrackInfo()>.

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
Otherwise, return the total play time of the CD in seconds.

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

sub _Conn($)
{ my($this)=@_;

  my($C,$welcome)=conn("$this->{HOST}:$this->{PORT}");
  if (! defined $C)
  { warn "$::cmd: conn(host=$this->{HOST},port=$this->{PORT}): $!\n";
    return undef;
  }

  return $C;
}

=item SetDiscId(I<discid>)

Set the CDDB disc id for this CD.

=cut

sub SetDiscId($$)
{ $_[0]->{DISCID}=$_[1];
}

=item DiscId()

Return the CDDB disc id for this CD
as an integer
by consulting the CDDB daemon.

=cut

sub DiscId()
{ my($this)=@_;

  return $this->{DISCID} if exists $this->{DISCID};

  $this->_StatIf() || return undef;

  my $C = $this->_Conn();
  return if ! defined $C;

  my $qry = "DISCID ".$this->_TrksNSecs();
  my($code,$etc)=command($C,$qry);
  if (! defined $code)
  { warn "$::cmd: no response to: $qry\n";
    return undef;
  }
  
  if ($code !~ /^200/
   || $etc !~ /disc\s*id\s*is\s*([\da-f]{8})/i
     )
  { warn "$::cmd: bad response to: $qry\n\t$code $etc\n";
    return undef;
  }

  return $this->{DISCID}="$1";	## was hex($1)
}

=item Query()

Match this disc against the database.
Returns an array of hashrefs of the form B<[I<categ>,I<title>,I<discid>]>.
Note that a single discid may match multiple database entries.

=cut

sub Query($)
{ my($this)=@_;

  my @p = $this->_ProbeCache();
  if (@p)
  { $this->Read($p[0]->[0]);
    return [$this->Category(),$this->Title(),$this->DiscId()];
  }

  my $discid = $this->DiscId();
  if (! defined $discid)
  { warn "$::cmd: cs::CDDB: no discid!\n";
    return ();
  }

  my $C = $this->_Conn();
  return if ! defined $C;

  my($code,$etc,@additional)=command($C,"CDDB QUERY $discid ".$this->_TrksNSecs());
  return () if ! defined $code || $code !~ /^2/;

  my @matches;

  if ($code =~ /^.1/)
  { for my $a (@additional)
    { if ($a !~ /^(\S+)\s+([a-f0-9]{8})\s+/)
      { warn "$::cmd: cs::CDDB: QUERY $discid: bad close match: $a\n";
      }
      else
      { push(@matches,[$1,$',$2]);
      }
    }
  }
  else
  { if ($etc !~ /^(\S+)\s+$discid\s+/)
    { warn "$::cmd: cs::CDDB: QUERY $discid: bad exact match: $etc\n";
    }
    else
    { push(@matches,[$1,$',$discid]);
    }
  }

  return @matches;
}

# return the files in the cache matching the discid, if any
# contrain ourselves to the specified category if supplied
sub _ProbeCache($;$)
{ my($this,$cat)=@_;

  my $discid = $this->DiscId();
  return () if ! defined $discid;

  my @cats;

  if (defined $cat)
  { @cats=$cat;
  }
  else
  { @cats=cs::Pathname::dirents($cs::CDDB::CacheDir);
  }

  my @hits;

  my $file;
  for my $c (@cats)
  { $file=cachefile($c,$discid);
    push(@hits,[$c,$file]) if -s $file;
  }

  return @hits;
}

=item Read(I<category>)

Return the xmcd database entry as an array of lines,
comments included.
If the caller has a cached entry in B<$HOME/.cddb/I<category>/I<discid>>
then the contents of that will be returned instead.

=cut

sub Read($$)
{ my($this,$category)=@_;

  my $discid = $this->DiscId();
  if (! defined $discid)
  { warn "$::cmd: cs::CDDB: no discid!\n";
    return ();
  }

  my $cachefield="READ/$category/$discid";
  if (exists $this->{$cachefield})
  { return @{$this->{$cachefield}};
  }

  my @caches = $this->_ProbeCache($category);
  if (@caches && open(CACHED,"< $caches[0]->[1]\0"))
  {
    my @entry;
    local($_);
  
    while (defined($_=<CACHED>))
    { chomp;
      push(@entry,$_);
    }
    close(CACHED);

    $this->_Cache($category,$discid,@entry);

    return @entry;
  }

  my $C = $this->_Conn();
  return if ! defined $C;

  my($code,$etc,@additional)=command($C,"CDDB READ $category $discid");
  return () if ! defined $code || $code ne 210;

  $this->_Cache($category,$discid,@additional);
  $this->_CacheSave($category,$discid,@additional);

  return @additional;
}

=item ReadField(I<fieldname>)

Wrapper for the B<Read()> method
which collects the specified I<field> from the database entry
as a single value
(concatenating multiple lines).

=cut

sub ReadField($$)
{ my($this,$field)=@_;

  my $cat = $this->Category();
  return undef if ! defined $cat;

  my @e = grep(s/^$field=//, $this->Read($cat));
  return undef if ! @e;

  my $v = join('',@e);

  $v =~ s/^\s+//;
  $v =~ s/\s+$//;

  $v;
}

# stash the results of a look up
sub _Cache
{ my($this,$category,$discid,@lines)=@_;
  
  $this->{CATEGORY}=$category;
  $this->{"READ/$category/$discid"}=[@lines];
}

# archive a lookup
sub _CacheSave
{ my($this,$category,$discid,@lines)=@_;

  my $file = cachefile($category,$discid);
  if (! -s $file && open(CACHE,"> $file\0"))
  { for (@lines)
    { print CACHE $_, "\n";
    }
    close(CACHE);
  }
}

=item SetCategory(I<category>)

Set the category of this disc.

=cut

sub SetCategory($$)
{ $_[0]->{CATEGORY}=$_[1];
}

=item Category()

Return the category of this disc.

=cut

sub Category($)
{ my($this)=@_;

  return $this->{CATEGORY} if exists $this->{CATEGORY};

  my @matches;

  @matches=$this->_ProbeCache();
  if (@matches > 0)
  {
    if (@matches > 1)
    { warn "$::cmd: cs::CDDB: multiple matches in cache!\n";
      return undef;
    }

    return $matches[0]->[0];
  }

  @matches=$this->Query();
  
  if (! @matches)
  { warn "$::cmd: cs::CDDB: no matches for ".$this->DiscId()."\n";
    return undef;
  }

  if (@matches > 1)
  { ## warn "$::cmd: cs::CDDB: multiple matches:\n";
    ## for my $m (@matches)
    ## { warn "\t$m->[0]\t$m->[1]\n";
    ## }
    return undef;
  }

  my($cat,$title)=@{$matches[0]};

  $this->{CATEGORY}=$cat;
  $this->{MATCHTITLE}=$title;

  return $cat;
}

=item DTitle()

B<DISCOURAGED>

Return the Artist/Title field B<DTITLE> from the CDDB record.
This is a really fucking stupid idea;
these should be completely separate fields
but obviously the CDDB designer had no brain.

Please use the B<Artist()> and B<Title()> methods instead;
sadly they are mere wrappers for this method
but at least we can pretend the db was well designed.

=cut

sub DTitle()
{ my($this)=@_;

  return $this->{DTITLE} if exists $this->{DTITLE};

  local($_);

  $_ = $this->ReadField(DTITLE);
  return undef if ! defined;

  $this->{DTITLE}=$_;

  if (
      m:(.*)\s+/\s+:
   || m:(.*)/\s+:
   || m:(.*)/+:
     )
  { $this->{ARTIST}=$1;
    $this->{TITLE}=$';

    $this->{ARTIST} =~ s:\s*/+\s*: -- :g;
    $this->{TITLE} =~ s:\s*/+\s*: -- :g;
  }
  else
  { warn "$0: bad DTITLE: $this->{DTITLE}\n\tsetting ARTIST == TITLE\n";
    $this->{ARTIST}=$_;
    $this->{TITLE}=$_;
  }

  $this->{DTITLE};
}

=item Artist()

Return the disc's artist.

=cut

sub Artist()
{ my($this)=@_;

  my $dtitle = $this->DTitle();
  return undef if ! defined $dtitle;
  return $this->{ARTIST};
}

=item Title()

Return the disc's title.

=cut

sub Title()
{ my($this)=@_;

  my $dtitle = $this->DTitle();
  return undef if ! defined $dtitle;
  return $this->{TITLE};
}

=item NTracks()

Return the number of tracks.

=cut

sub NTracks
{ scalar($_[0]->Tracks());
}

=item TrackInfo()

Return an array of hashrefs containing
track timing and names.

=cut

sub TrackInfo($)
{ my($this)=@_;

  if (! exists $this->{TRACKINFO})
  {
    my $cat = $this->Category();
    return () if ! defined $cat;

    $this->_StatIf() || return ();

    my @e = $this->Read($cat);
    return () if ! @e;

    my $T = $this->{TRACKS};

    for (@e)
    { if (/^TTITLE0*(\d+)=\s*/)
      { $T->[$1]->{TTITLE}.=$';
      }
      elsif (/^EXTT0*(\d+)=\s*/)
      { $T->[$1]->{EXTT}.=$';
      }
    }

    $this->{TRACKINFO}=$this->{TRACKS};
  }

  return @{$this->{TRACKINFO}};
}

sub _TrksNSecs($)
{ my($this)=@_;

  my @t = map($_->{OFFSET}, $this->Tracks());

  return scalar(@t)." @t ".$this->Length();
}

=back

=head1 ENVIRONMENT

CDDBSERVER, the default CDDB daemon in the form B<I<host>:I<port>>

=head1 SEE ALSO

CDDB_get(3), FreeDB(3), cddiscinfo(1cs), cdtoc(1cs), cdsubmit(1cs)

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au> 10sep2001

=cut

1;
