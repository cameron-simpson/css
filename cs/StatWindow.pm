#!/usr/bin/perl
#
# Control a sliding window of stats.
#	- Cameron Simpson <cs@zip.com.au> 02sep97
#

use strict qw(vars);

package cs::StatWindow;

sub new($;$)
{ my($class,$seq)=@_;
  $seq=0 if ! defined $seq;

  my($this)={ S => [],
	      D => [],
	      LOW => $seq,
	      SEQ => $seq,
	    };

  bless $this, $class;

  $this;
}

# add a datum
sub Add($$)
{ my($this,$datum)=@_;

  push(@{$this->{D}},$datum);
  push(@{$this->{S}},$this->{SEQ}++);
}

sub Seq($)
{ shift->{SEQ};
}

sub Data($)
{ my($this)=shift;
  wantarray ? ($this->{S}, $this->{D}) : $this->{D};
}

# crop the low end of the window
# or return current low end
sub Low($;$)
{ my($this,$low)=@_;
  return $this->{LOW} if ! defined $low;

  # drain expired data
  my($s,$d)=($this->{S}, $this->{D});
  while (@$s && $s->[0] < $low)
  { shift(@$s);
    shift(@$d);
  }

  $this->{LOW}=$s->[0];
}

1;
