#!/usr/bin/perl
#
# Assorted stats routines.
#	- Cameron Simpson <cs@zip.com.au> 30oct96
#

use strict qw(vars);

use cs::Image::Mapped;
use cs::Math;

package cs::Stats;


sub new
	{ my($class,$units)=@_;
	  $units='' if ! defined $units;

	  bless { OKEYS => [],
		  KPOS  => {},
		  STATS => [{},{},{}],	# hits, counts, widths
		  UNITS => $units,
		}, $class;
	}

sub Units
	{ my($this,$units)=@_;
	  if (defined $units)
		{ $this->{UNITS}=$units;
		}

	  $this->{UNITS};
	}

sub Keys
	{ my($this,$ordering)=@_;

	  my(@keys)=@{$this->{OKEYS}};

	  return @keys if ! defined $ordering;

	  local($cs::Stats::This);

	  if (! ref $ordering)
		{ if ($ordering eq HITS)
			{ $cs::Stats::This=$this->{STATS}->[0];
			  return sort
				 { $cs::Stats::This->{$a} <=> $cs::Stats::This->{$b} }
				 @keys;
			}

		  if ($ordering eq COUNT)
			{ # print STDERR "ordering by COUNT [@keys]\n";
			  $cs::Stats::This=$this->{STATS}->[1];
			  return sort
				 { $cs::Stats::This->{$a} <=> $cs::Stats::This->{$b} }
				 @keys;
			}

		  warn "unknown ordering \"$ordering\"";
		  return @keys;
		}

	  sort $ordering @keys;
	}

sub _orderCmp
	{ &$cs::Stats::Cmp($a,$b); }
sub Order
	{ my($this,$ordering,$reverse)=@_;
	  $reverse=0 if ! defined $reverse;

	  if (! defined $ordering)
		{ warn "no ordering for Order()";
		  return;
		}

	  # in case the orderer wants to use it
	  local($cs::Stats::This)=$this;
	  local($cs::Stats::Cmp)=$ordering;

	  $this->{OKEYS}=($reverse ? [ sort _orderCmp $this->Keys() ]
				   : [ reverse sort _orderCmp $this->Keys() ]);

	  my($n)=0;

	  # recompute key positions
	  for (@{$this->{OKEYS}})
		{ $this->{KPOS}->{$_}=$n++;
		}
	}

sub KeyPos
	{ my($this,$key)=@_;

	  return undef if ! exists $this->{KPOS}->{$key};

	  $this->{KPOS}->{$key};
	}

sub Hit
	{ my($this,$key,$count,$hits,$width)=@_;
	  $count=1 unless defined $count;
	  $hits=1 unless defined $hits;

	  my($stats)=$this->{STATS};

	  warn "hit(undef)" if ! defined $key;
	  if (! exists $stats->[0]->{$key})
		{ $stats->[0]->{$key}=$hits;
		  $stats->[1]->{$key}=$count;
		  $stats->[2]->{$key}=(defined $width ? $width : 1);
		  push(@{$this->{OKEYS}},$key);
		  $this->{KPOS}->{$key}=$#{$this->{OKEYS}};
		}
	  else
	  { $stats->[0]->{$key}+=$hits;
	    $stats->[1]->{$key}+=$count;
	    if (defined $width)
		{ if ($stats->[2]->{$key} != $width)
			{ warn "change of width for \"$key\"!";
			  $stats->[2]->{$key}=$width;
			}
	  }
	}

sub Hits
	{ my($this,$key)=@_;

	  if (defined $key)
		{ return $this->{STATS}->[0]->{$key};
		}

	  # no key - total of all hits
	  my($hits)=$this->{STATS}->[0];
	  my($sum)=0;

	  for $key (keys %$hits)
		{ $sum+=$hits->{$key};
		}

	  $sum;
	}

sub Count
	{ my($this,$key)=@_;
	  $this->{STATS}->[1]->{$key};
	}

sub Width
	{ my($this,$key)=@_;
	  return $this->{STATS}->[2]->{$key} if defined $key;

	  my($w)=$this->{STATS}->[2];
	  my($sum)=0;

	  for $key (keys %$w)
		{ $sum+=$w->{$key};
		}

	  $sum;
	}

sub Sum
	{ my($this)=shift;
	  my($counts)=$this->{STATS}->[1];
	  my($sum)=0;
	  my($key);

	  for $key ($this->Keys())
		{

# if (! exists $counts->{$key}) { warn "!exist($key)"; }
# elsif (! defined $counts->{$key}) { warn "!defined($key)"; }

		  $sum+=$counts->{$key};
		}

	  $sum;
	}

sub AverageSlot
	{ my($this)=shift;
	  my($slots)=scalar $this->Keys();

	  return undef if $slots == 0;

	  $this->Sum()/$slots;
	}

sub AverageHit
	{ my($this)=shift;
	  my($hits)=$this->Hits();

	  return undef if $hits == 0;

	  $this->Sum()/$hits;
	}

sub MinKey
	{ my($this,$part)=shift;
	  $part=1 if ! defined $part;	# 0=hits, 1=count

	  my($key,$val);

	  if ($part == 0)
		# hits
		{ for ($this->Keys())
			{ if (! defined $key
			   || $this->Hits($_) < $val)
				{ $key=$_;
				  $val=$this->Hits($key);
				}
			}
		}
	  else
		# counts
		{ for ($this->Keys())
			{ if (! defined $key
			   || $this->Count($_) < $val)
				{ $key=$_;
				  $val=$this->Count($key);
				}
			}
		}

	  return undef if ! defined $key;

	  ($key,$val);
	}

sub MaxKey
	{ my($this,$part)=shift;
	  $part=1 if ! defined $part;	# 0=hits, 1=count

	  my($key,$val);

	  if ($part == 0)
		# hits
		{ for ($this->Keys())
			{ if (! defined $key
			   || $this->Hits($_) > $val)
				{ $key=$_;
				  $val=$this->Hits($key);
				}
			}
		}
	  else
		# counts
		{ for ($this->Keys())
			{ if (! defined $key
			   || $this->Count($_) > $val)
				{ $key=$_;
				  $val=$this->Count($key);
				}
			}
		}

	  return undef if ! defined $key;

	  ($key,$val);
	}

sub Min	{ my($this,$part)=shift;
	  my($key,$val)=$this->MinKey($part);
	  defined $key ? $val : undef;
	}
sub Max	{ my($this,$part)=shift;
	  my($key,$val)=$this->MaxKey($part);
	  defined $key ? $val : undef;
	}

# return a new object containing the stats for keys matching the function
sub Grep
	{ my($this,$grepfn)=@_;
	  my($o)=new cs::Stats;

	  for ($this->Keys())
		{ if (&$grepfn($this,$_))
			{ $o->Hit($_,$this->Count($_),$this->Hits($_));
			}
		}

	  $o;
	}

sub LabelKey
	{ my($this,$key,$labelfn)=@_;

	  return $key if ! defined $labelfn;

	  &$labelfn($this,$key);
	}

# sample a range
sub Sample
	{ my($this,$low,$high,$step,$keyfn)=@_;
	  my($s)=new cs::Stats;

	  my(@keys)=$this->Keys();
	  my($bar,$nbar,$key,$val);

	  for ($bar=$low; $bar<=$high; $bar=$nbar)
		{ $nbar=$bar+$step;

		  $val=0;
		  for (@keys)
			{ $key=(ref $keyfn ? &$keyfn($this,$_) : $_);
			  if ($key >= $bar && $key < $nbar)
				{ $val+=$this->Count($key);
				}
			}

		  $s->Hit($bar,$val);
		}

	  $s;
	}

# Recatalogue a stats collection
sub Remap
	{ my($this,$mapfn)=@_;
	  my($s)=new cs::Stats;

	  my($key);

	  for ($this->Keys())
		{ $key=&$mapfn($this,$_);
		  $s->Hit($key,$this->Count($_),$this->Hits($_));
		}

	  $s;
	}

sub _BarKeyPos
	{ my($this,$key)=@_;

	  ($ox,$cs::Stats::_oy-($this->KeyPos($key)+0.5)*$cs::Stats::_height);
	}

@cs::Stats::_BarPalette=(ORANGE,LIGHTBLUE,GREEN);
# return an image containing a bar-graph of the data
# mode can be RAW or DIFFERENTIAL
sub BarGraph
	{ my($this,$resx,$resy,$labelfn,$mode)=@_;

	  $resx=600		if ! defined $resx;
	  $resy=$resx		if ! defined $resy;
	  $mode=DIFFERENTIAL	if ! defined $mode;

	  my($im); $im=new cs::Image::Mapped $resx, $resy;

	  my($sum)=$this->Sum();
	  my($max)=$this->Max();
	  my($total_width)=$this->Width();
	  my($wscale)=$resx/$total_width;

	  my(@keys)=$this->Keys();
	  my($nval)=scalar(@keys);

	  my(%keypos)=();
	  my($n);

	  $n=0;
	  for (@keys)
		{ $keypos{$_}=$n++;
		}

	  my($axes)=$im->Colour(RED);
	  my($col);

	  return $im if $nval == 0 || $max == 0;

	  local($cs::Stats::dx,$cs::Stats::dy)=(0.8*$resx,0.8*$resy);
	  local($cs::Stats::_ox,$cs::Stats::_oy)=(0.1*$resx,0.9*$resy);
	  local($cs::Stats::_height)=$cs::Stats::dx/$nval;
	  my($x,$y,$p,$p2);
	  my(@lines)=();
	  my(@labels)=();

	  # axes and tick marks
	  push(@lines,[$axes,$cs::Stats::_ox,$cs::Stats::_oy,$cs::Stats::_ox+$cs::Stats::dx,$cs::Stats::_oy]);
	  push(@lines,[$axes,$cs::Stats::_ox,$cs::Stats::_oy,$cs::Stats::_ox,$cs::Stats::_oy-$cs::Stats::dy]);

	  my($avg)=$this->AverageSlot();
	  $x=$cs::Stats::_ox+$cs::Stats::dx*$avg/$max;
	  push(@lines,[$axes,$x,$cs::Stats::_oy,$x,$cs::Stats::_oy-$cs::Stats::dy]);
	  push(@labels,$im->StringSpec(sprintf("average=%d%%",$avg*100/$max),
					[$x,$cs::Stats::_oy+17],BELOW));

	  # tick marks
	  my($r);

	  for ($r=0; $r<=100; $r+=10)
		{
		  $x=$cs::Stats::_ox+$r*$cs::Stats::dx/100;
		  push(@lines,[$axes,$x,$cs::Stats::_oy,$x,$cs::Stats::_oy+5]);
		  push(@labels,$im->StringSpec("$r%",[$x,$cs::Stats::_oy+7],BELOW));
		}

	  my($key,$labeltext,@size,$l,$ox);

	  $ox=0;
	  MKLABEL:
	    for $key (@keys)
		{
		  $labeltext=$this->LabelKey($key,$labelfn);
		  next MKLABEL if ! defined $labeltext
			       || ! length $labeltext;
		  print STDERR "label=\"$labeltext\"\n";

		  @size=$im->StringSize($labeltext);
		  $p=[ $this->_BarKeyPos($key,$ox) ];
		  $ox+=$this->Width($key)*$wscale;
		  $p2=[ @$p ];
		  cs::Image::move(-5,0,$p);
		  $l=$im->StringSpec($labeltext,$p,LEFT);
		  push(@labels,$l);
		  push(@lines,[ $axes, @$p, @$p2 ]);
		}

	  push(@labels,$im->StringSpec("peak=".$max.$this->Units(),
					[ $cs::Stats::_ox+$cs::Stats::dx, $cs::Stats::_oy ], LEFTABOVE));

	  for (@labels)
		{
		  $im->FilledRectangle($_->{CORNERS},WHITE);
		}

	  for (@lines)
		{ $col=shift(@$_);
		  $im->Line($_,$col);
		}

	  my($count,$ocount);

	  if ($mode eq DIFFERENTIAL)
		{ $col=$im->Colour(BLUE);
		}

	  $n=0;
	  undef $ocount;
	  KEY:
	    for $key (@keys)
		{
		  $count=$this->Count($key);
		  # next KEY if $count == 0;

		  $ocount=$count if ! defined $ocount;

		  $x=$cs::Stats::_ox+1;
		  $y=$cs::Stats::_oy-1-$n*$cs::Stats::_height;

		  if ($mode eq RAW)
			{ $col=$im->Colour($cs::Stats::_BarPalette[$n%@cs::Stats::_BarPalette]);
			  $p=
				[ $x, $y,
				  $x+$cs::Stats::dx*$count/$max,
				  $y-$cs::Stats::_height+1,
				];
			}
		  else	{ $p=
				[ $x+$cs::Stats::dx*$ocount/$max, $y,
				  $x+$cs::Stats::dx*$count/$max, $y-$cs::Stats::_height+1,
				];
			}

		  $im->FilledRectangle($p,$col);
		}
	    continue
		{
		  $n++;
		  $ocount=$count;
		}

	  for (@labels)
	  	{
		  $im->String($_->{STRING},
			      @{$_->{SPOS}},
			      BLACK);
		}

	  $im;
	}

@cs::Stats::_PiePalette=(ORANGE,BLUE,GREEN);
# return an image containing a pie-graph of the data
sub PieGraph
	{ my($this,$thresh,$resx,$resy,$labelfn)=@_;

	  $thresh=0.05	if ! defined $thresh;
	  $resx=600	if ! defined $resx;
	  $resy=$resx	if ! defined $resy;

	  my($im); $im=new cs::Image::Mapped $resx, $resy;

	  my($sum)=$this->Sum();

	  my($key,$aspect);
	  my($langle,$rangle,$left)=(0,0,0);
	  my($portion,$from,$mid,$to);
	  my($dx,$dy)=($im->DX(),$im->DY());
	  my($cx,$cy)=($dx/2,$dy/2);
	  my($rx,$ry)=($cx*0.8,$cy*0.8);
	  my($labeltext,@bounds,@size);
	  my($x1,$x2,$y1,$y2,$lx,$ly,$ldx,$ldy);
	  my(@slices,@labels);
	  my($l);

	  SLICE:
	    for $key (reverse sort { $this->Count($a) <=> $this->Count($b) }
				   $this->Keys())
		{ $portion=$this->Count($key)/$sum;
		  last SLICE if $portion < $thresh;

		  if ($left)
			{ $from=$langle-$portion*360;
			  $mid=$langle-$portion*180;
			  $to=$langle;
			  $langle=$from;
			}
		  else	{ $from=$rangle;
			  $to=$from+$portion*360;
			  $mid=$from+$portion*180;
			  $rangle=$to;
			}

		  push(@slices,{ FROM => $from, TO => $to, });

		  $mid=cs::Math::deg2rad($mid);

		  $labeltext=$this->LabelKey($key,$labelfn);
		  @size=$im->StringSize($labeltext);
		  $ldx=int($size[0]/2)+1;
		  $ldy=int($size[1]/2)+1;

		  # centre of label
		  ($lx,$ly)=($cx+$rx*cos($mid),$cy+$ry*sin($mid));

		  my($l)=$im->StringSpec($labeltext,[ $lx,$ly ],CENTRE);
		  $im->StringConfine($l);
		  push(@labels,$l);

		  $left = !$left;
		}

	  for (@labels)
		{
		  $im->FilledRectangle($_->{CORNERS},WHITE);
		}

	  my($slicenum);

	  $slicenum=0;
	  for (@slices)
		{
		  $im->FilledEllipse([$cx,$cy],
				     $rx,$ry,
				     $_->{FROM},$_->{TO},
				     $im->Colour($cs::Stats::_PiePalette[$slicenum]));
		  $slicenum=($slicenum+1) % @cs::Stats::_PiePalette;
		}

	  for (@labels)
	  	{
		  $im->String($_->{STRING},
			      @{$_->{SPOS}},
			      BLACK);
		}

	  $im;
	}

1;
