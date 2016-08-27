#!/usr/bin/perl

require 'flush.pl';
use cs::Page;
use cs::LinedSource;
use cs::CacheSource;
use cs::IO;

print "running...\n";

die if ! defined ($tail=new cs::Source TAIL, "/tmp/foo");

die if ! defined ($cache=cs::IO::cacheFile());

die if ! defined (tie @F, cs::LinedSource, FILE, $cache);

$pgsiz=4;

die if ! defined ($P=new cs::Page \@F, $pgsiz);

$nlines=0;

while (defined($_=$tail->GetLine()))
	{ if (length)
		{ seek($cache,0,2);
		  print $cache $_;
		  &flush($cache);

		  $nlines++;
		  if ($nlines > $pgsiz)
			{ $P->ScrollDown(1);
			}

		  for $i (0..$pgsiz-1)
			{ if (! defined ($_=$P->Line($i)))
				{ print "no line $i in \$P\n";
				}
			  else
			  { print "$i: $_";
			  }
			}
		}
	  else
	  { print "sleep 1\n";
	    sleep(1);
	  }
	}

exit(0);

while (1)
	{ for $i (0..$pgsiz-1)
		{ if (! defined ($_=$P->Line($i)))
			{ print "no line $i in \$P\n";
			}
		  else
		  { print;
		  }
		}

	  $P->ScrollDown($pgsiz);

	  print STDERR ":";
	  if (! defined ($_=$t->GetLine()) || ! length)
		{ print STDERR "\n";
		  exit(0);
		}

	  if (/^-/)
		{ $P->ScrollUp(2*$pgsiz);
		}
	  elsif (/^q/)
		{ exit(0);
		}
	  else
	  {};
	}

#for $i ((12,14,16,8,2000))
#	{ if (! defined ($_=$F[$i]))
#		{ print "$file, line $i: GetLine fails\n";
#		}
#	  else
#	  { print "$i: $_";
#	  }
#	}
