#!/usr/bin/perl
#
# Layout class for marked up text-like stuff.
# This is a base class and expects the geometry and semantic information
# to come from the super-class.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

use cs::Misc;

package cs::Layout;

$cs::Layout::XSep=1;

sub new
	{ my($class,$width,$attrs)=@_;
	  die "width undefined" if ! defined $width;

	  my($this)=
	  bless { DX	=> $width,	# width of scroll
		  DY	=> 0,		# height if constrained, 0 if not
		  AREA	=> [],		# list of available rectangles
		  X	=> 0,		# location of top left of next item
		  Y	=> 0,
		  NEXTY	=> 0,
		  XSEP	=> $cs::Layout::XSep,
		}, $class;

	  if (defined $attrs)
		{ for ($attrs)
			{ $this->{$_}=$attrs->{$_};
			}
		}

	  $this;
	}

# next drawing region
sub Area
	{ my($this)=@_;
	  my($A)=$this->{AREA};
	  my($a);

	  # no subareas? make sure the pseudo one is alligned
	  if (! @$A)
		{ $this->{Y}=$this->{NEXTY};
		}

	  @$A ? $A->[0] : $this;
	}

# toss the leading unused area
sub PopArea
	{ my($this)=@_;
	  if (@{$this->{AREA}})
		{
	  	  shift(@{$this->{AREA}});
		}
	}

# insert new things at the front
sub PushArea
	{ my($this)=shift;
	  my(@c)=caller;
	  unshift(@{$this->{AREA}},@_);
	}

# available width within which things should try to fit
sub Width
	{ my($this)=@_;
	  my($a)=$this->Area();
	  $a->{DX};
	}

sub Use
	{ my($this,$x,$y)=@_;
	  # my(@c)=caller;warn "Use(@_) from [@c]";
	  my($a)=$this->Area();

	  # release extant rectangle
	  $this->PopArea();

	  # note descent
	  # warn "this=".cs::Hier::h2a($this,0)."\na=".cs::Hier::h2a($a,0)."\nfrom [@c]";
	  $this->{NEXTY}=::max($this->{NEXTY},$a->{Y}+$y);

	  if ($x+$this->{XSEP} < $a->{DX})
		# narrower than available space
		# trim top left corner
		{
		  if ($a->{DY} > $y)
			# push lower rectangle
			{ $this->PushArea({ DX	=> $a->{DX},
					    DY	=> $a->{DY}-$y,
					    X	=> $a->{X},
					    Y	=> $a->{Y}+$y
					  });
			}

		  # push right-hand rectangle
		  $this->PushArea({ DX => $a->{DX}-$x-$this->{XSEP},
				    DY => $y,
				    X  => $a->{X}+$x+$this->{XSEP},
				    Y  => $a->{Y}
				  });
		}
	  else
	  # wider
	  # trim the top
	  {
	    if ($a->{DY} > $y)
	    	{ $this->PushArea({ DX	=> $a->{DX},
				    DY	=> $a->{DY}-$y,
				    X	=> $a->{X},
				    Y	=> $a->{Y}+$y
				  });
		}
	  }
	}

# place objects
sub Put
	{ my($this,@o)=@_;
	  my(@a);

	  my($it,$cut,@uncut,$x,$y,$width);

	  while (@o)
		{
		  $it=shift(@o);

		  $width=$this->Width();
		  ($cut,@uncut)=$it->CutToFit($width);
		  $x=$cut->Width();
		  while ($width < $x && $width < $this->{DX})
			{ $this->PopArea();
			  $width=$this->Width();
			  ($cut,@uncut)=$it->CutToFit($width);
			  $x=$cut->Width();
			}

		  # where to put the object
		  $a=$this->Area();

		  # stash the thing to render
		  # and queue any unrendered portion
		  push(@a,{ X => $a->{X}, Y => $a->{Y}, VALUE => $cut });
		  unshift(@o,@uncut);

		  # consume that space
		  $this->Use($x,$cut->Height());
		}

	  @a;
	}

1;
