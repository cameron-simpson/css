#!/usr/bin/perl
#
# A slotted stats object, with fixed-width slots and means for
# dropping samples across the slots.
#	- Cameron Simpson <cs@zip.com.au> 30oct96
#

use strict qw(vars);

use cs::Misc;
use cs::Math;

package cs::Stats::Slots;

sub new
	{ my($class,$base,$width,$field,$threshold)=@_;
	  $width=1 if ! defined $width;	# arbitrary but workable
	  $field=SIZE if ! defined $field;
	  $threshold=0 if ! defined $threshold;

	  bless
	  { BASE => $base,	# lowest slot
	    WIDTH => $width,	# slot width
	    FIELD => $field,	# field from datum
	    DFLTTHRESHOLD => $threshold, # max slot total
	    SLOTS => [],	# an array of slots
	    STASHDATA => 0,	# don't keep originating data
	  }, $class;
	}

sub Slot
	{ my($this,$ndx)=@_;

	  my $slots = $this->{SLOTS};

	  if (! defined $slots->[$ndx])
		{ $slots->[$ndx]={ TOTAL => 0,
				   DATA => [],
				   THRESHOLD => $this->{DFLTTHRESHOLD},
				 };
		}

	  $slots->[$ndx];
	}

# add data from datum to a slot
# apply the threshold if ! $force and there is a threshold
# return how much was added
sub AddToSlot
{ my($this,$ndx,$datum,$value,$force)=@_;
  $value=$datum->{$this->{FIELD}} if ! defined $value;
  $force=0 if ! defined $force;

  my $slot = $this->Slot($ndx);

  if (! $force && $this->{THRESHOLD} > 0)
	{ $value=::min($value,$slot->{THRESHOLD}-$slot->{TOTAL});
	  return 0 if $value <= 0;
	}

  push(@{$slot->{DATA}}, [ $value, ($this->{STASHDATA} ? $datum : 0) ]);
  $slot->{TOTAL}+=$value;

  $value;
}

sub SlotNdx
	{ my($this,$offset)=@_;

	  my $base = $this->{BASE};

	  return undef if $offset < $base;

	  int( ($offset-$base)/$this->{WIDTH} );
	}

sub NdxLowOffset
	{ my($this,$ndx)=@_;
	  $this->{BASE}+$this->{WIDTH}*$ndx;
	}

# max slot index so far
sub MaxSlotNdx
	{ my $max = $#{shift->{SLOTS}};
	  $max > 0 ? $max : 0;
	}

# least defined slot
sub MinSlotNdx
	{ my($slots)=shift->{SLOTS};

	  for my $i (0..$#$slots)
	  { return $i if defined $slots->[$i];
	  }

	  my @c = caller;
	  warn "no defined slots, returning 0 [from @c]";
	  return 0;
	}

# take a datum and distribute it across the slots it overlaps
# return an array of [slotndx, value] indicating distribution
sub Distribute
{ my($this,$datum,$low,$high)=@_;
  if ($low >= $high)
  { my @c = caller;
    warn "low($low) >= high($high) from [@c]";
    return ();
  }

  my $base = $this->{BASE};

  return () if $high <= $base;	# no overlap

  my $field = $this->{FIELD};
  my $value = $datum->{$field};
  my $width = $this->{WIDTH};

  # clip to slot range
  if ($low < $base)
	{ $value*=($high-$base)/($high-$low);
	  $low=$base;
	}

  my $datumlen = $high-$low;

  my @ndx = $this->SlotNdx($low)..$this->SlotNdx($high);
  my @values = ();

  # allocate evenly across slots according to overlap
  for my $ndx (@ndx)
  {
    my $lbound = ::max($low, $this->NdxLowOffset($ndx));
    my $hbound = ::min($high,$lbound+$width);

    push(@values,$value*($hbound-$lbound)/$datumlen);
  }

  # now try to distribute as allocated

  # array showing distribution
  my @dist = ();

  my $leftover = 0;

  for my $i (0..$#ndx)
  {
    my $used     = $this->AddToSlot($ndx[$i],$datum,$values[$i]);
    my $overflow = $values[$i]-$used;

    push(@dist,[$ndx[$i], $values[$i]-$overflow]);

    if ($i < $#ndx)
    { $values[$i+1]+=$overflow;
    }
    else
    { $leftover+=$overflow;
    }
  }

  # if there was overflow, fill up from the front
  if ($leftover > 0)
  {
    FILL:
    for my $i (0..$#ndx)
    {
      my $used    = $this->AddToSlot($ndx[$i],$datum,$leftover);

      $dist[$i]->[1]+=$used;
      $leftover-=$used;
      last FILL if $leftover <= 0;
    }
  }

  # if there was still overflow, force it all in
  # as evenly as possible
  if ($leftover > 0)
  {
    warn "forced past threshold by $leftover for ".cs::Hier::h2a($datum,1);

    for my $i (0..$#ndx)
    {
      my $force = $leftover * $values[$i]/$value;

      $this->AddToSlot($ndx[$i],$datum,$force,1);
      $dist[$i]->[1]+=$force;
    }
  }

  @dist;
}

1;
