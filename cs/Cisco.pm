#!/usr/bin/perl
#
# Drive Cisco switches. Catalysts, really.
# Relies on my script "askcisco".
#	- Cameron Simpson <cs@zip.com.au> 28jan99
#

use strict qw(vars);

package cs::Cisco;

sub new
	{ my($class,$sw)=@_;

	  $sw=lc($sw);

	  bless { NAME => $sw }, $class;
	}

sub Ask	{ my $this = shift; ask($this->{NAME}, @_); }

sub Trunks
	{ my($this)=@_;

	  if (! exists $this->{TRUNKS})
	  {
	    my @n = $this->Ask('show cdp neighbors');

	    if (@n)
	    {
	      $this->{TRUNKS}={};

	      for (@n)
	      { if (m:^\s*(\d+)/(\d+)\s+(\S+)\s+(\d+)/(\d+)\s+(\S+)\s+(.*):)
		{ $this->{TRUNKS}->{"$1/$2"}={ PORT => "$1/$2",
					       DEVID => $3,
					       RPORT => "$4/$5",
					       RPLATFORM => $6,
					       CAP => $7,
					     };
		}
	      }
	    }
	  }

	  $this->{TRUNKS};
	}

sub eth2cisco
	{ local($_)=@_;
	  s/:/-/g;
	  s/[\da-f]+/(0 x (2-length($&))).$&/eg;
	  $_;
	}

sub cisco2eth
	{ local($_)=@_;
	  s/-/:/g;
	  s/0([\da-f])/$1/g;
	  $_;
	}

sub CamsByVLan
	{ my($this,@vlans)=@_;

	  if (! exists $this->{CAM})
	  { $this->{CAM}={};
	  }

	  for my $vlan (@vlans)
	  {
	    my @cam = $this->Ask("show cam dynamic $vlan");

	    for (@cam)
	    { if (m:^(\S+)\s+([\da-f][\da-f]-[\da-f][\da-f]-[\da-f][\da-f]-[\da-f][\da-f]-[\da-f][\da-f]-[\da-f][\da-f])\s+(\d+)/(\d+):)
	      { my $eth = cisco2eth($2);

		$this->{CAM}->{$eth}={ VLAN => $1,
				       MAC => $eth,
				       PORT => "$3/$4",
				     };
	      }
	    }

	  }

	  $this->{CAM};
	}

sub ask
	{ my($sw,$swcmd)=@_;

	  ## warn "ask $sw $swcmd...\n";
	  my @ans;

	  if (! open(ASKCISCO,"askcisco $sw $swcmd |"))
	  { warn "$::cmd: can't pipe from askcisco: $!\n";
	  }
	  else
	  { local $_;

	    while (defined ($_=<ASKCISCO>))
	    { ## print STDERR '.';
	      chomp;
	      push(@ans,$_);
	    }
	    close(ASKCISCO);
	    ## print STDERR "\n";
	  }

	  wantarray ? @ans : join("\n",@ans)."\n";
	}

1;
