use cs::RFC822;

while (<STDIN>)
	{ chomp;
	  @p=cs::RFC822::parseaddrs($_);
	  print "p=[@p]\n";
	  while (@p)
		{ ($a,$t)=(shift(@p),shift(@p));
		  print "$a: $t\n";
		}
	}
