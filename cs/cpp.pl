#!/usr/bin/perl
#
# Parse C Preprocessor directives.
#	- Cameron Simpson <cs@zip.com.au>
#

use cs::Misc;

package cpp;

&initstate('');

sub initstate	# (pfx) -> void
	{ for (@_)
		{ eval "\@${_}Directives=();
			\@${_}Exprs=();
			\@${_}IfParts=()";
		}
	}

sub getline	# (FILE) -> line or undef
	{ local($FILE)=shift;
	  local($_);

	  while(defined($_ = &sloshline($FILE)))
		{ if (/^#\s*(ifdef|ifndef|if)\s+(.*)$/)		{ &pushif($1,$2); }
		  elsif (/^#\s*el(ifdef|ifndef|if)\s+(.*)$/)	{ &popif;
			  					  &pushif($1,$2);
								}
		  elsif (/^#\s*else$/ || /^#\s*else\W/)		{ &else; }
		  elsif (/^#\s*endif$/ || /^#\s*endif\W/)	{ &popif; }
		# elsif (/^#/)					{ }
		  else						{ return $_; }
		}

	  return undef;
	}

# At the moment it just handles slosh extension.
# This is probably a Good Thing.
#
# Possible problem: maybe it should retain the \\\n to
# protect against compilers with line length problems.
# This may have ramifications for some patterns used later.
# I hope not.
#
undef $lastline;
undef $thisline;
sub sloshline	# (FILE) -> line or undef
	{ local($FILE)=shift;
	  local($_,$line);

	  return undef if !defined($_=<$FILE>);

	  s/\r?\n$//o;
	  LINE:
	    while (/\\$/o)
		{ chop;
		  last LINE if !defined($line=<$FILE>);
		  $_.=$line;
		  s/\r?\n$//o;
		}
	  
	  $_;
	}

sub pushif	# (directive,expr) -> void
	{ local($directive,$expr)=@_;

	  push(@Directives,$directive);
	  push(@Exprs,$expr);
	  push(@IfParts,1);
	}

sub else	# void -> void
	{ if ($#IfParts >= $[)
		{ push(@IfParts,!pop(@IfParts));
		}
	  else
	  { print STDERR "hit #else when no #ifs active!\n";
	  }
	}

sub popif	# void -> void
	{ if ($#IfParts >= $[)
		{ pop(@Directives);
		  pop(@Exprs);
		  pop(@IfParts);
		}
	  else
	  { print STDERR "hit #endif when no #ifs active!\n";
	  }
	}

sub syncstate	# (scope) -> @difference
	{ local($scope)=@_;
	  local($d,$i,$j,$lastOld,$lastNew,$bound,$emitelse);
	  local(@cppstuff);
	  local(@OldDirectives,@OldExprs,@OldIfParts);

	  eval "\@OldDirectives=\@${scope}Directives;
		\@OldExprs=\@${scope}Exprs;
		\@OldIfParts=\@${scope}IfParts";

	  @cppstuff=();

	  $lastOld=$#OldDirectives;
	  $lastNew=$#Directives;

	  $bound=&'min($lastOld,$lastNew);

	  # determine how much of outer state is unchanged
	  for ($i=0; $i <= $bound; $i++)
		{ last if ($Directives[$i] ne $OldDirectives[$i]
		      	|| $Exprs[$i] ne $OldExprs[$i]
		      	|| $IfParts[$i] ne $OldIfParts[$i]);
		}
	
	  # see if a #else will do a layer of #if
	  $emitelse=($i <= $bound
		  && $Directives[$i] eq $OldDirectives[$i]
	   	  && $Exprs[$i] eq $OldExprs[$i]);

	  # yes, we don't need to undo that level
	  if ($emitelse)
		{ $i++;
		}

	  # undo all the other levels though
	  for ($j=$i; $j <= $lastOld; $j++)
		{ push(@cppstuff,"#endif");
		}

	  # toggle this level
	  if ($emitelse)
		{ push(@cppstuff,"#else");
		}

	  # instate the new levels
	  for ($j=$i; $j <= $lastNew; $j++)
		{ if ($IfParts[$j])
			# reproduce directive
			{ push(@cppstuff,"#$Directives[$j]\t$Exprs[$j]");
			}
		  else
		  # produce inversion of directive
		  { $d=$Directives[$j];
		    if ($d eq "if")
			{ push(@cppstuff,"#$Directives[$j]\t!($Exprs[$j])");
			}
		    elsif ($d eq "ifdef")
			{ push(@cppstuff,"#ifndef\t$Exprs[$j]");
			}
		    elsif ($d eq "ifndef")
			{ push(@cppstuff,"#ifndef\t$Exprs[$j]");
			}
		    else
		    { print STDERR "Danger Will Robinson! - unknown directive \"$d\"\n";
		    }
		  }
		}
	
	  # Save new state.
	  eval "\@${scope}Directives=\@Directives;
		\@${scope}Exprs=\@Exprs;
		\@${scope}IfParts=\@IfParts";

	  return @cppstuff;
	}

1;
