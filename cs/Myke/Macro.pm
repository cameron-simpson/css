#!/usr/bin/perl
#
# Handle macro definitions.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::Macro;

sub new
	{ my($class,$context,$level,$macro,$args,$mvalue)=@_;

	  bless { MACRO		=> $macro,
		  ARGS		=> $args,
		  VALUE		=> $mvalue,
		  CONTEXT	=> $context,
		  LEVEL		=> $level,
		}, $class;
	}

sub Eval	# (dp) -> @values
	{ my($this)=shift;

	  my(@v);

	  if (! ref $this->{VALUE})
		{ my($expr,$tail)=cs::Myke::Expr::parse($this->{CONTEXT},
							$this->{VALUE},"");

		  die "$::cmd: huh? stuff left after parse!\n"
		     ."\tparsed: $this->{VALUE}\n"
		     ."\tleft with: $tail"
		  if length $tail;

		  $this->{VALUE}=$expr;
		}

	  cs::Myke::Expr::e_expr($this->{VALUE},@_);
	}

1;
