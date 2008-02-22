#!/usr/bin/perl
#
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::ByteRangeSource;

@cs::$pfx::ISA=qw();

sub new
	{ my($class,$start,$len)=(shift,shift,shift);
	  die "no subsource!" if ! @_;

	  my($ds);

	  if (@_ == 1 && ref($_[0]))	{ $ds=@_; }
	  else				{ $ds=new cs::Source @_;
					  return undef if ! defined $ds;
					}

	  bless { START	=> $start,
		  LENGTH=> $len,
		  DS	=> $ds,
		}, $class;
	}

sub _Abs
	{ my($this,$where)=@_;
	  $where+$this->{START};
	}
sub _Max	# just past end
	{ my($this)=@_;
	  $this->{START}+$this->{LEN};
	}

sub _SeekTo
	{ my($this,$abswhere)=@_;

	  my($ds)=$this->{DS};
	  my($subwhere)=$ds->Tell();

	  if ($subwhere < $abswhere)
		{ $subwhere=$ds->Skip($abswhere-$subwhere);
		  return $subwhere == $abswhere;
		}
	  elsif ($subwhere > $abswhere)
		{ return $ds->Seek($abswhere);
		}
	  else	{ return 1;	# already there!
		}
	}
1;
