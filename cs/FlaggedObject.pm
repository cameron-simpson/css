#!/usr/bin/perl
#
# An object containing flags. Inherited from, not a base class.
#	- Cameron Simpson <cs@zip.com.au> 26aug97
#

use strict qw(vars);

use cs::Misc;
use cs::Flags;
use cs::Object;

package cs::FlaggedObject;

@cs::Flags::ISA=(cs::Object);

sub _GetFlagsRef
{ my($this)=@_;

  my $flags;

  if (! exists $this->{FLAGS})
  { $flags=new cs::Flags;
  }
  elsif (! ref $this->{FLAGS})
  { $flags=new cs::Flags;

    if (exists $this->{FLAGS}
     && defined $this->{FLAGS})
    { $flags->Set(split(/:/,$this->{FLAGS}));
    }
  }
  else
  { $flags=bless $this->{FLAGS}, cs::Flags;
  }

  $this->{FLAGS}=$flags;
}

# fetch the flags
sub Flags
{ my($flags)=_GetFlagsRef(shift);
  wantarray ? $flags->Members() : $flags;
}

sub Set	{ my($this)=shift; _GetFlagsRef($this)->Set(@_); }
sub Clear{ my($this)=shift; _GetFlagsRef($this)->Clear(@_); }
sub Intersect{ my($this)=shift; _GetFlagsRef($this)->Intersect(@_); }
sub TestAll{ my($this)=shift; _GetFlagsRef($this)->TestAll(@_); }

1;
