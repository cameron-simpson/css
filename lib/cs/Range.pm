#!/usr/bin/perl
#
# Manage a range. Ints, initially.
#	- Cameron Simpson <cs@zip.com.au> 20jun98
#

=head1 NAME

cs::Range - a set of positive numeric ranges, such as found in a newsrc file

=head1 SYNOPSIS

use cs::Range;

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Range;

$cs::Range::_Epsilon=1;	# integers

# pattern for numbers - 3 subpatterns
$cs::Range::_numptn='\\(-?\\d+(\\.\\d*)?\\)|(\\d+(\\.\\d*)?)';
## warn "numptn=[$cs::Range::_numptn]";

=head1 OBJECT CREATION

=over 4

=item new cs::Range (I<irange>,I<roundfn>)

Create a new B<cs::Range> with initial values form the text string I<irange>,
which is applied with the B<AddRange()> method.

=cut

sub new
{ my($class,$iRange,$roundfn)=@_;
  $iRange='' if ! defined $iRange;
  $roundfn=\&noRounding if ! defined $roundfn;

  my($this)=
  bless { ROUNDFN => $roundfn,
	  RANGE   => [],
	}, $class;

  $this->AddText($iRange);

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item AddText(I<range>)

Add the range in the supplied text string to this object.

=cut

sub AddText
{ my($this,$iRange)=@_;

  my($low,$high);

  while ($iRange =~ /$cs::Range::_numptn/o)
  {
    $low=eval $&;	# get low end
    $iRange=$';

    if ($iRange =~ /^\s*-\s*($cs::Range::_numptn)/o)
    { $high=eval $1;
      $iRange=$';
    }
    else
    { $high=$low;
    }

    if ($low > $high)
    { warn "bogus range $low - $high";
    }
    else
    { $this->Add($low,$high);
    }
  }
}

sub noRounding { wantarray ? shift : @_ }

=item Add(I<low>,I<high>)

Add the values from I<low> to I<high> inclusive.
I<high> is optional, and defaults to I<low>.

=cut

sub Add
{ my($this,$low,$high)=@_;
  $high=$low if ! defined $high;

  return if $low > $high;	# in case

  my($ranges)=$this->{RANGE};

  if (! @$ranges)
  { push(@$ranges,[$low,$high]);
  }
  else
  { my($i,$j,$r);

    #### skip preceeding ranges
    PRE:
      for ($i=$[; $i<=$#$ranges; $i++)
	{ $r=$ranges->[$i];
	  last PRE if $r->[1] >= $low;
	}

    # index of first overlapping segment
    $j=$i;

    #### add overlapping ranges to this one, and discard
    CURR:
      while ($i <= $#$ranges)
	{ $r=$ranges->[$i];
	  last CURR if $r->[0] > $high;
	  $low=$r->[0] if $r->[0] < $low;
	  $high=$r->[1] if $r->[1] > $high;

	  $i++;
	}

    # $i is now past the overlaps
    # replace all overlaps with new range
    splice(@$ranges,$j,$i-$j,[$low,$high]);
    $i=$j;
  }
}

=item Del(I<low>,I<high>)

Delete the values from I<low> to I<high> inclusive.
I<high> is optional, and defaults to I<low>.

=cut

sub Del
{ my($this,$low,$high)=@_;
  $high=$low if ! defined $high;

  my($ranges)=$this->{RANGE};

  { my($i,$j,$r,@nr,$l,$h);

    #### skip preceeding ranges
    PRE:
      for ($i=$[; $i <= $#$ranges; $i++)
	{ $r=$ranges->[$i];
	  last PRE if $r->[1] >= $low;
	}

    # index of first overlapping segment
    $j=$i;

    #### crop overlapping ranges and keep non-empty cropped items
    CURR:
      while ($i <= $#$ranges)
	{ $r=$ranges->[$i];
	  last CURR if $r->[0] > $high;

	  if ($r->[0] < $low)
		# preserve bottom half
		{ $l=$r->[0];
		  $h=::min($r->[1],$low-$cs::Range::_Epsilon);
		  push(@nr,[$l,$h]) if $l <= $h;
		}

	  if ($r->[1] > $high)
		# preserve top half
		{ $l=::max($r->[0],$high+$cs::Range::_Epsilon);
		  $h=$r->[1];
		  push(@nr,[$l,$h]) if $l <= $h;
		}

	  $i++;
	}

    # $i is now past the overlaps
    # replace all overlaps with new range
    splice(@$ranges,$j,$i-$j,@nr);
    $i=$j;
  }
}

sub _Coalesce
{ my($this)=@_;

  my($ranges)=$this->{RANGE};

  my($i,$j,$r,$r2);

  COAL:
    for ($i=$[; $i < $#$ranges; $i++)
    { $r=$ranges->[$i];

      my($up)=$r->[1]+$cs::Range::_Epsilon;

      ABUT:
	for ($j=$i+1; $j <= $#$ranges; $j++)
	{ $r2=$ranges->[$j];
	  last ABUT if $up < $r2->[0];
	  $up=$r2->[1]+$cs::Range::_Epsilon;
	}
      ## $j is now the first non-abutting block

      splice(@$ranges,$i,$j-$i,[$r->[0],$ranges->[$j-1]->[1]]);
    }
}

=item SubRanges()

Return an array of arrayrefs, each of the form B<[I<low>,I<high>]>.

=cut

sub SubRanges($;$)
{ my($this)=@_;

  $this->_Coalesce();

  my(@sub)=();

  for my $r (@{$this->{RANGE}})
  { push(@sub,[$r->[0],$r->[1]]);
  }

  @sub;
}

=item Text()

Return the textual transcription of the object.

=cut

sub Text($)
{ my($this)=@_;

  $this->_Coalesce();

  local($_)='';

  for my $r (@{$this->{RANGE}})
  { $_.=', ' if length;
    $_.=( $r->[0] >= 0
	? $r->[0]
	: "($r->[0])"
	);
    $_.='-'
       .( $r->[1] >= 0
	? $r->[1]
	: "($r->[1])"
	) if $r->[1] > $r->[0];
  }

  $_;
}

=item Enum()

Return an array of every number in the range in order.

=cut

sub Enum($)
{ my($this)=@_;

  my(@e);

  for my $r (@{$this->{RANGE}})
  { for my $i ($r->[0]..$r->[1])
    { push(@e,$i);
    }
  }

  @e;
}

=item Bounds()

Return the lowest and highest values in the range.
Returns the array B<(0,0)> with an empty range.

=cut

sub Bounds($)
{ my($this)=@_;

  my($range)=$this->{RANGE};

  return (0,0) if ! @$range;

  ($range->[0]->[0],$range->[$#$range]->[1]);
}

=item InRange(I<n>)

Test whether the value I<n> is in the range.

=cut

sub InRange($$)
{ my($this,$n)=@_;

  my($range)=$this->{RANGE};
  my($i);

  for ($i=0; $i <= $#$range; $i++)
  {
    # test first to prune faster
    return 1 if $range->[$i]->[1] >= $n
	     && $range->[$i]->[0] <= $n;
  }

  0;
}

=item Invert()

Return a new B<cs::Range> consisting of the gaps in this range.

=cut

# return a new range with all the gaps
sub Invert($)
{ my($this)=@_;

  my($gaps)=new cs::Range;

  $this->_Coalesce();
  my($low,$high)=$this->Bounds();

  my($range)=$this->{RANGE};
  my($i,$glow,$ghigh);

  # walk backwards through elements because we know
  # the Add() method is an insertion and would be O(n^2)
  # if we walked forward
  for my $j (0..$#$range-1)
  { $i=$#$range-$j-1;

    $glow =$range->[$i]->[1]+$cs::Range::_Epsilon;
    $ghigh=$range->[$i+1]->[0]-$cs::Range::_Epsilon;

    if ($glow <= $ghigh)
    { $gaps->Add($glow,$ghigh);
    }
  }

  $gaps;
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
