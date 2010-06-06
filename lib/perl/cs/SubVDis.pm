#!/usr/bin/perl
#
# VDis-like module for a subportion of a screen.
# Note that since it's very simple minded cursor motions must preceed
# output to a different SubVids, much as a seek should preceed alternating
# reads and writes in stdio. Sync and Refresh etc are fairly meaningless
# to a SubVDis.
#	- Cameron Simpson <cs@zip.com.au> 13oct96
#

use strict qw(vars);

package cs::SubVDis;

@cs::SubVDis::ISA=(cs::VDis);

sub new
	{ my($class,$super,$nrows,$row0,$ncols,$col0)=@_;
	  my($this)={};
	  my($sx,$sy)=$super->Size();

	  if (! defined $nrows)
		{ $nrows=$sy;
		  $row0=0;
		}

	  if (! defined $ncols)
		{ $ncols=$sx;
		  $col0=0;
		}

	  $this->{X0}=$col0; $this->{DX}=$ncols;
	  $this->{Y0}=$row0; $this->{DY}=$nrows;
	  $this->{SUPER}=$super;

	  bless $this, $class;

	  print STDERR "SubVDis=", cs::Hier::h2a($this,1), "\n";

	  $this;
	}

sub DESTROY
	{ 
	}

sub Size{ my($this)=shift;

	  ($this->{DX},$this->{DY});
	}
sub Move{ my($this,$x,$y)=@_;
	  $this->{SUPER}->Move($x+$this->{X0},$y+$this->{Y0});
	}

sub _super
	{ my($method,$this)=(shift,shift);
	  $this->{SUPER}->$method(@_);
	}
sub Out	{ _super('Out',@_); }
sub Flush { _super('Flush',@_); }
sub Rows { _super('Rows',@_); }
sub Cols { _super('Cols',@_); }
sub Sync { _super('Sync',@_); }
sub Bold { _super('Bold',@_); }
sub NoBold { _super('NoBold',@_); }
sub Under { _super('Under',@_); }
sub NoUnder { _super('NoUnder',@_); }
sub Normal { _super('Normal',@_); }
sub Bell { _super('Bell',@_); }

1;
