#!/usr/bin/perl
#
# Myke - my make program
#
#	- Cameron Simpson <cs@zip.com.au> 02nov97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::ALL;

package cs::Myke;	cs::ALL::useAll();

sub new
	{ my($class)=@_;

	  bless { MACROS	=> {},
		  LEVEL		=> 0,
		  DEPS		=> {},
		  RULES		=> [],	# first to last
		}, $class;
	}

sub FindMacro
	{ my($this,$macro,$dp)=@_;
	  
	  return undef if ! exists $this->{MACROS}->{$macro};

	  $this->{MACROS}->{$macro};
	}

sub SetMacro
	{ my($this,$context,$level,$macro,$args,$mvalue)=@_;
	  $this->{MACROS}->{$macro}=new cs::Myke::Macro ($context,
							 $level,
							 $macro,
							 $args,
							 $mvalue);
	}

1;
