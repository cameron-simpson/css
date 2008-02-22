#!/usr/bin/perl
#
# Utility functions based around srand() and rand().
#	- Cameron Simpson <cs@zip.com.au>, 01aug94
#

srand(time^(40503*$$));

package random;

sub pick	# @list -> element
	{ @_[$[+int(rand($#_-$[+1))];
	}

1;
