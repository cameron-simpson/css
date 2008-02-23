#!/usr/bin/perl

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Upd;
use cs::Sink;
use cs::Source;
use cs::LogMap;

package cs::Log;

sub new
	{ my($class,$logspec,$forinput)=@_;
	  my($s);

	  $forinput=0 if ! defined $forinput;

	  if ($logspec eq '-')
		{ $s=($forinput ? (new cs::Source FILE, STDIN)
				: (new cs::Sink FILE, STDOUT)
		     );
		}
	  else
	  { local($_);

	    if ($logspec =~ /^[:\w]/)
		{ $_=cs::LogMap::logspec($logspec);
		  if (! length)
			{ cs::Upd::err("$'cmd: no map for $logspec\n");
			  return undef;
			}
		}
	    else
	    { $_=$logspec;
	    }

	    $s=($forinput ? (new cs::Source PATH, $_)
			  : (new cs::Sink APPEND, $_)
	       );
	  }
	}

1;
