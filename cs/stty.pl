#!/usr/bin/perl
#
# OS independant interface to stty.
#	- Cameron Simpson, 04jul94, cs@zip.com.au
#
# stty'get(FILE) -> (speed,cols,rows)
#	BUG: actually tests /dev/tty at present.
#

package stty;

sub get	# FILE -> (speed,cols,rows)
	{ local($FILE)=@_;

	  if ($FILE !~ /'/)	# tidy up FILE argument
		{ local($caller)=caller;
		  $FILE="$caller'$FILE";
		}

	  open(TTY,">&$FILE")
	  	|| die "$'cmd: stty'get($FILE): can't dup $FILE: $!\n";
	  local($fd)=fileno(TTY);

	  local($_,$stty);
	  $bsdstty="stty everything 2>&1 1>&$fd $fd>&-";
	  $attstty="stty -a <&$fd $fd<&- 2>&1";
	  $stty="exec $fd>/dev/tty $fd</dev/tty; $bsdstty; $attstty\n";
	  $_=`$stty`;

	  close(TTY);

	  # print STDERR "stty get: $_";

	  local($speed,$rows,$cols);

	  if (/(\d+)\s*baud/)	{ $speed = $1+0; }
	  if (/(\d+)\s*rows/
	   || /rows\s*=\s*(\d+)/){$rows = $1+0; }
	  if (/(\d+)\s*columns/
	   || /columns\s*=\s*(\d+)/){$cols = $1+0; }

	  # print STDERR "stty get: speed=$speed, rows=$rows, cols=$cols\n";

	  ($speed,$cols,$rows);
	}

1;
