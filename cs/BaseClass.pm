#!/usr/bin/perl
#
# Basic routines to supply the META component of a base class.
#	- Cameron Simpson <cs@zip.com.au> 17may96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Object;

package cs::BaseClass;

@cs::BaseClass::ISA=qw(cs::Object);

sub DESTROY
{ my($this)=shift;
  $this->SUPER::DESTROY(@_);
}

# the meta data for the immediate superclass
sub _Meta
{ my($this)=@_;
  die if @_ != 1;
  $this->{META}={} if ! exists $this->{META};
  $this->{META};
}

# the meta data for the supers of the immediate super
sub Meta
{ my($_meta)=_Meta(@_);
  $_meta->{META}={} if ! exists $_meta->{META};
  $_meta->{META};
}

1;
