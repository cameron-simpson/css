#!/usr/bin/perl
#
# A source attached to a byte-range.
#	- Cameron Simpson <cs@zip.com.au> 23jul96
#
# Note: This is built on top of an existing Source.
#	If you make more than one of these from the same source
#	(eg if you were extracting things from an archive)
#	it is necessary to read them in order since the underlying
#	Source object can't move backwards.
#	Since the positioning is deferred until the Read() or Skip()
#	call, the objects can be made in any order.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Source;

package cs::SubSource;

@cs::SubSource::ISA=(cs::Source);

sub new
	{ my($class,$s,$start,$length)=@_;
	  my($this)=(new cs::Source Source, $s);

	  return undef if ! defined $this;

	  $this->{START}=$start;
	  $this->{BOUND}=$length;

	  bless $this, $class;
	}

sub Read
	{ my($this,$size)=@_;

	  $this->_Align() || return undef;
	  if (! defined $size)
		{ $size=$this->{DS}->ReadSize();
		}

	  $size=::min($this->{BOUND}-$this->Tell(),$size);

	  return '' if $size == 0;

	  $this->SUPER::Read($size);
	}

sub Skip
	{ my($this,$n)=@_;

	  $this->_Align() || return undef;
	  $n=::min($n,$this->{BOUND}-$this->Tell());
	  $this->SUPER::Skip($n);
	}

sub _Align
	{ my($this)=shift;
	  my($target)=$this->{START}+$this->Tell();

	  # ensure we're at the right spot
	  if ($this->{DS}->Tell() != $target)
		{ my($skipped)=$this->{DS}->SkipTo($target);
		  return undef if ! defined $skipped;
		  if ($skipped != $target)
			{ warn "couldn't skip to $target (got to $skipped)";
			  return undef;
			}
		}

	  1;
	}

sub DESTROY
	{ shift->SUPER::DESTROY();
	}
1;
