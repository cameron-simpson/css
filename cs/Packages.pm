#!/usr/bin/perl
#
# Package code.
#
# &abs(symbol)	Return absolute symbol by attaching package of caller of caller
#		if necessary.
#		Doesn't touch the empty string.
#		Resolves 'name into main'name.
#

use strict qw(vars);

package cs::Packages;

require Exporter;
@cs::Packages::ISA=qw(Exporter);
@cs::Packages::EXPORT=qw(CPack);

sub CPack
	{ my($var);
	  my($called)=caller(1);

	  for $var (@_)
		{ if ($var !~ /'|::/)	{ print STDERR "$var => ";
					  $var="${called}::$var";
					  print STDERR "$var\n";
					}
		}
	}

1;
