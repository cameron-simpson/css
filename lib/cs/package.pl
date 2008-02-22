#!/usr/bin/perl
#
# Package code.
#
# &abs(symbol)	Return absolute symbol by attaching package of caller of caller
#		if necessary.
#		Doesn't touch the empty string.
#		Resolves 'name into main'name.
#

# Change name by direct fiddling with $_[0].
# Only works in Perl5.
sub Abs	{ if ($_[0] =~ /^'/)	{ $_[0]="main$_[0]"; }
	  elsif ($_[0] =~ /'/)	{}
	  else			{ $_[0]=caller(1)."'$_[0]"; }
	}

# this used to work by assignment to $_[0] but perl4 coredumps
# now we pass the name of the variable rather than the variable itself
sub abs	{ local($_v)=shift;
	  local($_);

	  if ($_v !~ /'/)
		{ $_v=caller(0)."'$_v"; }

	  $_=eval "\$$_v";

	  local(@c)=caller;
	  if (! length)	{}
	  elsif (/^'/)	{ $_='main'.$_; }
	  elsif (!/'/)	{ local($p,$f,$l);
			  ($p,$f,$l)=caller(1);
			  $_=$p."'".$_;
			}

	  if ($_ ne $_v)
			{ #print STDERR "$_v -> $_\n";
			  #print STDERR "eval \"\$$_v=\$_\" ($_)\n";
			  eval "\$$_v=\$_";
			}

	  $_;
	}

1;
