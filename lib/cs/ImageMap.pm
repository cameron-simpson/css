#!/usr/bin/perl
#
# Code to do things for image maps.
#	- Cameron Simpson <cs@zip.com.au> 30dec96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Geometry;
use cs::Hier;

package cs::ImageMap;

@cs::ImageMap::ISA=qw();

sub new
	{ my($class,$s)=@_;
	  my($this)=bless {}, $class;

	  $this->Load($s);
	}

sub Load
	{ my($this,$s)=@_;

	  local($_);

	  return undef if ! defined ($_=$s->GetLine()) || ! length;

	  my($h)=cs::Hier::h2a($_);

	  return undef if ! ref $h;

	  $this->{''}=$h;

	  HOTSPOT:
	    while (defined ($_=$s->GetLine()) && length)
		{ chomp;
		  s/^\s+//;
		  next HOTSPOT if ! length;

		  if (/^(\w+)\s+(\d+)\s+(\d+)\s+(\S+)/)
			{ $this->{$1}=[$2+0,$3+0,$4];
			}
		  else	{ warn "bad hotspot: \"$_\"";
			}
		}

	  $this;
	}

sub Save
	{ my($this,$s)=@_;

	  $s->Put(cs::Hier::h2a($this->{''},0), "\n");

	  my($p);

	  for ($this->Keys())
		{ $p=$this->{$_};
		  $s->Put($_,
			  "\t", $p[0], ' ', $p[1],
			  "\t", $p[2], "\n");
		}
	}

sub Keys
	{ my($this)=shift;

	  grep(length,keys %$this);
	}

sub ImageMap
	{ my($this,$imghref,$alt,$name,$action)=shift;
	  my(@h)=();

	  push(@h,"<IMG SRC=$imghref alt=\"$alt\"");
	  if (defined $x)	{ push(@h," WIDTH=$x"); }
	  if (defined $y)	{ push(@h," HEIGHT=$y"); }
	  if (defined $action)	{ push(@h," ISMAP"); }
	  if (defined $name)	{ push(@h," USEMAP=#$name"); }
	  push(@h,">\n");

	  if (defined $name)
		{ push(@h,"<MAP NAME=$name>\n");
		  for ($this->MapRegions())
			{ push(@h,"  <AREA HREF=",
				  $_->{HREF},
				  " SHAPE=",
				  	$_->{SHAPE},
				  " ALT=\"",
					$_->{KEY}.': '.$_->{HREF},
					"\"",
				  " COORDS=",
					join(",",@{$_->{COORDS}}),
				  ">\n");
			}
		  push(@h,"</MAP>\n");
		}

	  @h;
	}

sub MapRegions
	{ my($this)=shift;
	  my(@r)=();

	  my($x,$y,$ref);

	  KEY:
	    for $key ($this->Keys())
		{ ($x,$y,$ref)=@{$this->{$key}};
		  $r=$this->NearRadius($x,$y);
		  next KEY if ! defined $r;
		  push(@r,{ KEY   => $key,
			    SHAPE => CIRCLE,
			    COORDS=> [ $x,$y,$r ],
			    HREF  => $ref,
			  });
		}

	  @r;
	}

sub FindSpot
	{ my($map,$x,$y)=@_;

	  for ($this->Keys())
		{ return $_ if $this->{$_}->[0] == $x
			    && $this->{$_}->[1] == $y;
		}

	  undef;
	}

sub Points
	{ my($this)=shift;
	  my(@p)=();

	  for ($this->Keys())
		{ push(@p,${$this->{$_}}[0],	# x
			  ${$this->{$_}}[1]);	# y
		}

	  @p;
	}

# return half the distance to the nearest point
sub NearRadius
	{ my($this,$x,$y)=@_;
	  my(@p)=$this->Nearest($x,$y,1);
	  return undef if ! @p;
	  sqrt(($x-$p[0])*($x-$p[0])+($y-$p[1])*($y-$p[1]))/2;
	}

sub Nearest
	{ my($this,$x,$y,$omitself)=@_;
	  $omitself=0 if ! defined $omitself;
	  cs::Geometry::nearest($x,$y,$this->Points());
	}

sub NearSpot
	{ my($this,$x,$y,$omitself)=@_;
	  $omitself=0 if ! defined $omitself;
	  my(@p)=$this->Nearest($x,$y,$omitself);
	  return undef if ! @p;
	  $this->FindSpot(@p);
	}

1;
