#!/usr/bin/perl
#
# Package for images with colour tables.
#	- Cameron Simpson <cs@zip.com.au> 05sep96
#
# Migrate to GD from GIF.	- cameron, 01nov97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Math;

package cs::Image::Mapped;

use GD;

@cs::Image::Mapped::ISA=(cs::Image);

$cs::Image::Mapped::RGBFile='/usr/local/X11R6.3/lib/X11/rgb.txt';

# new image
sub new
	{ my($class,$type)=(shift,shift);

	  my($gd);

	  if (ref $type)
		# assume it's a source with an image (default GIF)
		# convert into {GIF|XBM|GD}
		{ my($s)=shift;
		  $type=(@_ ? shift : GIF);

		  my($FILE);

		  if (! defined ($FILE=$s->DupToFILE()))
			{ return undef;
			}

		  @_=$FILE;
		}

	  if ($type eq XY)
		{ $gd=new GD::Image (@_);
		}
	  elsif ($type eq GD::Image)
		{ $gd=shift;
		}
#	  elsif ($type eq GD)
#		{ my(@c)=caller;warn "from [@c], calling newFromGd(@_)";
#		  $gd=GD::Image::newFromGd(@_);
#		}
#	  elsif ($type eq GIF)
#		{ $gd=GD::Image::newFromGif(@_);
#		}
#	  elsif ($type eq XBM)
#		{ $gd=GD::Image::newFromXbm(@_);
#		}
	  else
	  { my(@c)=caller;
	    die "new cs::Image::Mapped(class=$class) called with wrong type ($type) from [@c]";
	  }

	  return undef if ! defined $gd;

	  my($this)={ GD => $gd,
		      COLOUR => 0,
		      WIDTH  => 1,
		      FONT   => gdFontSmall,
		      _COLOUR_STACK => [],
		      _FONT_STACK => [],
		    };

	  bless $this, $class;

	  # oddball almost-black colour for transparency
	  my($c123)=$gd->colorAllocate(1,2,3);
	  $gd->transparent($c123);
	  $gd->filledRectangle(0,0,$this->DX(),$this->DY(),$c123);

	  $this;
	}

# query
sub DX	{ my(@xy)=shift->{GD}->getBounds(); $xy[0]; }
sub DY	{ my(@xy)=shift->{GD}->getBounds(); $xy[1]; }

# data
sub Data
	{ my($this,$type)=@_;
	  $type=GIF if ! defined $type;

	  my($gd)=$this->{GD};

	  return $gd->gif() if $type eq GIF;
	  return $gd->gd()  if $type eq GD;

	  my(@c)=caller;
	  die "can't save as type \"$type\" from [@c]";
	}
sub Put
	{ my($this,$sink)=(shift,shift);
	  if (! ref $sink)
		{ my($s2);

		  ::need(cs::Sink);
		  if (! defined ($s2=new cs::Sink (FILE,$sink)))
			{ warn "$::cmd: can't dup($sink): $!\n";
			  return undef;
			}

		  $sink=$s2;
		}

	  $sink->Put($this->Data(@_));
	}

# return or set current colour index
# $colour	undef -> return current colour
#		previously allocated colour
#		rrggbb -> red green blue hex values
#		name -> colour name
# $closest	==> if not already present, allocate closest colour
#		    rather than a new one
#
sub Colour
	{ my($this,$colour,$closest)=@_;
	  return $this->{COLOUR} if ! defined $colour;
	  $closest=0 if ! defined $closest;

	  if ($colour =~ /^\d+$/)
		{}
	  else
	  {
	    my($gd)=$this->{GD};
	    my($r,$g,$b);

	    ($r,$g,$b)=c2rgb($colour);
	    warn "[$colour] -> ($r,$g,$b)";
	    return undef if ! defined $r;

	    $colour=$gd->colorExact($r,$g,$b);
	    warn "colorExact($r,$g,$b)=[$colour]";

	    if ($colour < 0)
		{ $colour=($closest
			? $gd->colorClosest($r,$g,$b)
			: $gd->colorAllocate($r,$g,$b));
		  warn "2nd colour($r,$g,$b)=[$colour]";
		}

	    return undef if $colour < 0;
	  }

	  $this->{COLOUR}=$colour;
	}

sub c2rgb
	{ my($colour)=@_;
	  my($r,$g,$b);

	  if (ref $colour)
		{ ($r,$g,$b)=@$colour;
		}
	  elsif ($colour =~ /^#?([\da-f]{6})$/i)
		{ ($r,$g,$b)=a2rrggbb($1);
		}
	  else
	  { ($r,$g,$b)=a2rgb($colour);
	    return () if ! defined $r;
	  }

	  ($r,$g,$b);
	}

sub a2rrggbb
	{ my($rrggbb)=@_;
	  if ($rrggbb !~ /^[-da-f]{6}$/i)
		{ my(@c)=caller;
		  die "a2rrggbb($rrggbb) from [@c]";
		}

	  my($r,$g,$b)=map(hex($_),/(..)(..)(..)/);

	  ($r,$g,$b);
	}

sub a2rgb
	{ my($colour)=@_;

	  ::need(cs::Source);
	  my($s)=cs::Source::open($cs::Image::Mapped::RGBFile);
	  return undef if ! defined $s;

	  $colour=~s/[-_\s]+//g;
	  $colour=lc($colour);

	  my($r,$g,$b,$cname);
	  local($_);

	  LINE:
	    while (defined($_=$s->GetLine()) && length)
		{ next LINE unless /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S.*\S)/;

		  ($r,$g,$b,$cname)=($1+0,$2+0,$3+0,$4);

		  $cname=~s/[-_\s]+//g;
		  $cname=lc($cname);

		  return ($r,$g,$b) if $cname eq $colour;
		}

	  ();
	}

sub Width
	{ my($this,$width)=@_;

	  if (defined $width)
		{ $this->{WIDTH}=$width;
		}

	  $this->{WIDTH};
	}

sub PushColour
	{ my($this,$ndx)=@_;
	  push(@{$this->{_COLOUR_STACK}},$this->{COLOUR});
	  $this->{COLOUR}=$ndx;
	}
sub PopColour
	{ my($this)=shift;
	  my($ndx)=pop(@{$this->{_COLOUR_STACK}});

	  if (defined $ndx)
		{ $this->{COLOUR}=$ndx;
		}
	  else	{ warn "${this}->PopColour(): nothing to pop";
		}

	  $ndx;
	}

sub PushFont
	{ my($this,$font)=@_;
	  push(@{$this->{_FONT_STACK}},$this->{FONT});
	  $this->{FONT}=$font;
	}
sub PopFont
	{ my($this)=shift;
	  my($font)=pop(@{$this->{_FONT_STACK}});

	  if (defined $font)
		{ $this->{FONT}=$font;
		}
	  else	{ warn "${this}->PopFont(): nothing to pop";
		}

	  $font;
	}

sub String
	{ my($this,$string,$x,$y,$colour)=@_;

	  if (defined $colour)
		{ $colour=$this->Colour($colour);
		}
	  else	{ $colour=$this->{COLOUR};
		}

	  # print STDERR "String(string=[$string],x=$x,y=$y,colour=$colour)\n";
	  gdImageString($this->{GD},
			$this->{FONT},
			$x,$y,$string,$colour);
	}

sub StringSize
	{ my($this,$string)=@_;
	  # (8*length($string),14);	# XXX - blatent guess for gdFontLarge
	  (6*length($string),11);	# XXX - blatent guess for gdFontSmall
	}
sub StringBounds
	{ my($this,$string,$x,$y)=@_;
	  my($dx,$dy)=$this->StringSize($string);

	  # print STDERR "StringBounds(string=[$string],x=$x,y=$y)\n";

	  ( $x, $y, $x+$dx, $y+$dy );
	}

sub StringSpec
	{ my($this,$text,$p,$align,$padding)=@_;
	  $align=CENTRE if ! defined $align;
	  $padding=1 if ! defined $padding;

	  my($x,$y)=@$p;
	  my($dx,$dy)=$this->StringSize($text);
	  my(@s);

	  if ($align eq CENTRE)		{ @s=(-$dx/2,-$dy/2); }
	  elsif ($align eq LEFT)	{ @s=(-$dx,-$dy/2); }
	  elsif ($align eq LEFTBELOW)	{ @s=(-$dx,0); }
	  elsif ($align eq LEFTABOVE)	{ @s=(-$dx,-$dy); }
	  elsif ($align eq RIGHT)	{ @s=(0,-$dy/2); }
	  elsif ($align eq RIGHTBELOW)	{ @s=(0,0); }
	  elsif ($align eq RIGHTABOVE)	{ @s=(0,-$dy); }
	  elsif ($align eq ABOVE)	{ @s=(-$dx/2,-$dy); }
	  elsif ($align eq BELOW)	{ @s=(-$dx/2,0); }
	  else
	  { die "can't align to \"$align\"";
	  }

	  $x+=$s[0];
	  $y+=$s[1];

	  { STRING  => $text,
	    CORNERS => [ $x-$padding,     $y-$padding,
			 $x+$dx+$padding, $y+$dy+$padding,
		       ],
	    SPOS    => [ $x, $y ],
	  };
	}

sub StringConfine
	{ my($this,$spec,$lx,$ly,$hx,$hy)=@_;
	  $lx=0 if ! defined $lx;
	  $ly=0 if ! defined $ly;
	  $hx=$this->DX()-1 if ! defined $hx;
	  $hy=$this->DY()-1 if ! defined $hy;

	  my($dx,$dy)=(0,0);

	  # confine X
	  if ($spec->{CORNERS}->[0] < $lx)
		{ $dx=$lx-$spec->{CORNERS}->[0]; }
	  elsif ($spec->{CORNERS}->[2] > $hx)
		{ $dx=$hx-$spec->{CORNERS}->[2]; }

	  # confine Y
	  if ($spec->{CORNERS}->[1] < $ly)
		{ $dy=$ly-$spec->{CORNERS}->[1]; }
	  elsif ($spec->{CORNERS}->[3] > $hy)
		{ $dy=$hy-$spec->{CORNERS}->[3]; }

	  cs::Image::move($dx,$dy,$spec->{CORNERS});
	  cs::Image::move($dx,$dy,$spec->{SPOS});
	}

sub Pixel
	{ my($this,$p,$colour)=@_;

	  $colour=$this->Colour($colour);

	  $this->PushColour($colour);
	  $this->{GD}->setPixel($p->[0],$p->[1],$this->{COLOUR});
	  $this->PopColour();
	}

# ellipse centred at $cp, width dx, height dy, start angle (degrees),
# end angle (degrees), [colour, line width]
sub Ellipse
	{ my($this,$cp,$dx,$dy,$start,$end,$colour,$width)=@_;

	  $width=$this->{WIDTH} unless defined $width;
	  $colour=$this->Colour($colour);

	  if ($start < 360)
		{ my($add)=int((-$start+360)/360)*360;
		  $start+=$add;
		  $end+=$add;
		  if ($end > 360)
			{ $this->Ellipse($cp,$dx,$dy,$start,360,$colour,$width);
			  $start=0;
			  $end%=360;
			}
		}

	  $this->PushColour($colour);
	  $this->{GD}->arc($cp->[0],$cp->[1],
			$dx*2,$dy*2,$start,$end,
			$this->{COLOUR},$width);
	  $this->PopColour();
	}

# make a nice polygon
sub FilledEllipse
	{ my($this,$cp,$dx,$dy,$start,$end,$colour,$width)=@_;
	  my(@cp)=@$cp;

	  $colour=$this->Colour($colour);

	  if ($start < 0)
		{ my($add)=int((-$start+360)/360)*360;
		  $start+=$add;
		  $end+=$add;
		  if ($end > 360)
			{ $this->FilledEllipse($cp,$dx,$dy,$start,360,$colour,$width);
			  $start=0;
			  $end%=360;
			}
		}

	  # compute angle size of step
	  my($dxy)=cs::Math::max($dx,$dy);	# nominal radius
	  my($cx,$cy)=@cp;
	  # print STDERR "cp=[@cp], dx=$dx, dy=$dy, $dxy=$dxy\n";

	  # desired segment angular size
	  my($step)=cs::Math::max(10/$dxy,$cs::Math::PI/16);

	  my($rad);

	  $start=cs::Math::deg2rad($start);
	  $end=cs::Math::deg2rad($end);
	  # print STDERR "start=$start, end=$end, step=$step\n";
	  for ($rad=$start; $rad < $end; $rad+=$step)
		{ push(@cp,$cx+$dx*cos($rad),$cy+$dy*sin($rad));
		}

	  $rad=$end;
	  push(@cp,$cx+$dx*cos($rad),$cy+$dy*sin($rad));

	  $this->FilledPolygon(\@cp,$colour);
	}

sub Line
	{ my($this,$ep,$colour,$width,$style)=@_;

	  warn "EndPoints (@$ep) must have exactly four elements"
		unless @$ep == 4;

	  $width=$this->{WIDTH} unless defined $width;
	  $colour=$this->Colour($colour);

	  $this->PushColour($colour);
	  if ($width != 1)
		{ warn "no width hook in GD, using 1 instead of $width";
		}
	  warn "line in colour $this->{COLOUR}";
	  $this->{GD}->line($ep->[0],$ep->[1],$ep->[2],$ep->[3],
			$this->{COLOUR});
	  $this->PopColour();
	}

sub Rectangle
	{ my($this,$ep,$colour,$width)=@_;

	  warn "EndPoints must have exactly four elements [@$ep]"
		unless @$ep == 4;

	  $width=$this->{WIDTH} unless defined $width;
	  $colour=$this->Colour($colour);

	  my($x1,$y1,$x2,$y2)=($ep->[0],$ep->[1],$ep->[2],$ep->[3]);
	  my($t);
	  if ($x1 > $x2)	{ $t=$x1; $x1=$x2; $x2=$t; }
	  if ($y1 > $y2)	{ $t=$y1; $y1=$y2; $y2=$t; }

	  $this->PushColour($colour);
	  $this->{GD}->rectangle($x1,$y1,$x2,$y2,
				$this->{COLOUR},$width);
	  $this->PopColour();
	}

sub FilledRectangle
	{ my($this,$ep,$colour)=@_;

	  warn "EndPoints must have exactly four elements [@$ep]"
		unless @$ep == 4;

	  $colour=$this->Colour($colour);

	  my($x1,$y1,$x2,$y2)=($ep->[0],$ep->[1],$ep->[2],$ep->[3]);
	  my($t);
	  if ($x1 > $x2)	{ $t=$x1; $x1=$x2; $x2=$t; }
	  if ($y1 > $y2)	{ $t=$y1; $y1=$y2; $y2=$t; }

	  $this->PushColour($colour);

	  warn "filledRect in colour $this->{COLOUR}";
	  $this->{GD}->filledRectangle($x1,$y1,$x2,$y2,
				  $this->{COLOUR});
	  $this->PopColour();
	}

sub Polygon
	{ my($this,$points,$colour,$borderWidth)=@_;

	  $borderWidth=$this->{WIDTH} unless defined $borderWidth;
	  $colour=$this->Colour($colour);

	  $this->PushColour($colour);
	  $this->{GD}->polygon($points, $colour, $borderWidth);
	  $this->PopColour();
	}

sub FilledPolygon
	{ my($this,$points,$colour)=@_;

	  $colour=$this->Colour($colour);

	  # print STDERR "FilledPolygon([@$points],$colour)\n";

	  $this->PushColour($colour);
	  $this->{GD}->filledPolygon($points, $colour);
	  $this->PopColour();
	}

# copy from one image to another
sub Copy
	{ my($this,$that,$src,$dest,$width,$height,$width2,$height2)=@_;
	  
	  $height2=$height if ! defined $height2;
	  $width2=$width   if ! defined $width2;

	  if ($width == $width2 && $height == $height2)
		{ $this->{GD}->copy($that->{GD},
			$dest->[0],$dest->[1],
			$src ->[0],$src ->[1],
			$width,$height);
		}
	  else	{ $this->{GD}->copyResized($that->{GD},
					$dest->[0],$dest->[1],
					$src ->[0],$src ->[1],
					$width2,$height2,
					$width,$height);
		}
	}

1;
