#!/usr/bin/perl
#
# I/O routines.
#

# This routine courtesy of Tom Christiansen <tchrist@cs.colorado.edu>
sub hasdata	# FILE -> boolean
	{ local($fh) = shift;
	  $fh =~ s/^([^']+)$/(caller)[$[]."'".$1/e;  
	  local($fd) = fileno($fh);
	  local($rin);
	  vec($rin, $fd, 1) = 1;
	  select($rin, 0, 0, 0) == 1;
	}
