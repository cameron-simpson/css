#!/usr/bin/perl
#
# A flag class based on a list of strings.
# Simple but not very efficient in either space or time.
#	- Cameron Simpson <cs@zip.com.au> 26aug97
#
# Reimplement as :-sep string.	- cameron 30jul98
#

use strict qw(vars);

use cs::Misc;

package cs::Flags;

sub new
{ my($class)=shift;

  my($f)=":";
  my($this)=(bless \$f, $class);

  $this->Set(@_);

  $this;
}

sub Members
{ my($this)=@_;
  
  grep(length,split(/:/,$$this));
}

# set the specified flags
sub Set
{ my($this)=shift;

  return if ! @_;

  ## warn "Set(@_)";

  FLAG:
    for my $flag (@_)
    { if ($flag =~ /\W/)
      { warn "$::cmd: bad flag \"$flag\"";
	next FLAG;
      }

      $$this.="$flag:" unless $$this =~ /:$flag:/;
    }

  ## warn "flags=[$$this]";
}

# clear the specified flags
sub Clear
{ my($this)=shift;

  for my $flag (@_)
  { $$this =~ s/:$flag:/:/;
  }
}

# which of specified flags are set?
sub Intersect
{ my($this)=shift;

  my(@f)=();

  ## {my(@c)=caller;warn "this=$this, f=[@f] from [@c]"}

  for my $flag (@_)
  { push(@f,$flag) if $$this =~ /:$flag:/;
  }

  @f;
}

# test for presence of a single flag
sub Test($$)
{ my($this,$f)=@_;

  $$this =~ /:$f:/;
}

# test for presence of all flags supplied
sub TestAll
{ my($this)=shift;

  ## warn "TestAll($$this vs [@_])";
  $this->Intersect(@_) == @_;
}

1;
