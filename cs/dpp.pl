#!/usr/bin/perl
#
# The smarts for the Dumb PreProcessor.
#	- Cameron Simpson, October 1993
#

require 'cs/pathname.pl';
require 'cs/open.pl';

package dpp;

undef %symbol;

@ipath=();
$before=''; undef $sub_before;
$after='';  undef $sub_after;

# define a symbol
sub define	# (this,that) -> void
	{ local($this,$that)=@_;

	  $verbose && print STDERR "define $this $that\n";

	  $symbol{$this}=$that;
	}

sub undefine	# (this) -> void
	{ delete $symbol{$_[0]};
	}

# run eval in package main
sub eval
	{ package main;

	  eval $_[0];
	}

sub set_before_after
	{ if (!defined($sub_before) || $sub_before ne $before)
		{ $sub_before=$before;
		  eval "sub before { $sub_before; }";
		  $verbose && print STDERR "sub before { $sub_before; }\n";
		}

	  if (!defined($sub_after) || $sub_after ne $after)
		{ $sub_after=$after;
		  eval "sub after { $sub_after; }";
		  $verbose && print STDERR "sub after { $sub_after; }\n";
		}
	}

sub preproc	# (text) -> expanded
	{ local($_)=shift;
	  local($lhs);

	  &set_before_after;
	  &before;
	
	  for ($lhs=''; /{(\w+)}/; )
		{ if (!defined($symbol{$1}))
			{ print STDERR "$'cmd: $ARGV, line $.: warning: can't replace {$1}, symbol not defined\n";
			  &define($1,"{$1}");
			}

		  $lhs.=$`.$symbol{$1};
		  $_=$';
		}

	  $_=$lhs.$_;

	  &after;

	  $_;
	}

sub preprocfile	# (filename,FILE) -> void
	{ local($ARGV,$FILE)=@_;
	  local(@ifLines,@ifExprs,@ifPart,@ifActive);

	  if ($FILE !~ /'/)
		{ local($caller); $caller=caller;
	  	  $FILE=$caller."'".$FILE;
		}

	  $verbose && print STDERR "processing $ARGV ($FILE)\n";

	  @ifLines=();
	  @ifExprs=();
	  @ifPart=();
	  @ifActive=();
	  while (<$FILE>)
		{ # $verbose && print STDERR "| $_";
		  s/\r?\n$//;
		  if (/^#\s*if\s+/)
			{ push(@ifLines,$.);
			  push(@ifExprs,$');
			  push(@ifPart,1);
			  push(@ifActive,( !@ifActive
				       ||  $ifActive[$#ifActive]
					 )
				      && &if($'));
			}
		  elsif (/^#\s*else\b/)
			{ while (@ifPart && !$ifPart[$#ifPart])
				{ print STDERR
					("$'cmd: $ARGV, line $.: popping unclosed #if $ifExprs[$#ifExprs] from line $ifLines[$#ifLines]\n");
				  $xit=1;
				  &popif;
				}

			  if (! @ifActive)
				{ print STDERR "$'cmd: $ARGV, line $.: #else without matching #if\n";
				  $xit=1;
				}
			  else
			  { $ifActive[$#ifActive] = !$ifActive[$#ifActive]
						 && (@ifActive == 1
						  || $ifActive[$#ifActive-1]);
			    $ifPart[$#ifPart] = 0;
			  }
			}
		  elsif (/^#\s*endif\b/)
			{ if ($#ifExprs < $[)
				{ print STDERR
					("$'cmd: $ARGV, line $.: #endif without matching #if\n");
				  $xit=1;
				}
			  else
			  { &popif;
			  }
			}
		  elsif (@ifActive && !$ifActive[$#ifActive])
			{ }
		  elsif (/^#\s*include\s+/)
			{ &inc($ARGV,$.,$'); }
		  elsif (/^#\s*define\s+/)
			{ $_=$';
			  if (/^(\w+)\s*/)
				{ $sym=$1; $text=&preproc($');
				  &define($sym,&preproc($text));
				}
			  else
			  { print STDERR "$'cmd: $ARGV, line $.: syntax error in #define $_\n";
			    $xit=1;
			  }
			}
		  elsif (/^#\s*eval\s+/)
			{ $_=$';
			  if (/^(\w+)\s*/)
				{ $sym=$1; $perl=&preproc($');
				  $text=&eval($perl);
				  if ($@)
					{ print STDERR "$'cmd: $ARGV, line $.: [$perl]: $@\n";
					  $xit=1;
					}
				  else
				  { &define($sym,$text);
				  }
				}
			  else
			  { print STDERR "$'cmd: $ARGV, line $.: syntax error in #eval $_\n";
			    $xit=1;
			  }
			}
		  elsif (/^#\s*echo\b\s*/)
			{ $_=$';
			  print &preproc($_), "\n";
			}
		  elsif (/^#\s*warn\b\s*/)
			{ $_=$';
			  print STDERR "$'cmd: $ARGV, line $.: ", &preproc($_), "\n";
			}
		  elsif (/^#\s*error\b\s*/)
			{ $_=$';
			  print STDERR "$'cmd: $ARGV, line $.: ", &preproc($_), "\n";
			  $xit=1;
			}
		  else
		  { print &preproc($_), "\n";
		  }
		}

	  while ($#ifExprs >= $[)
		{ print STDERR "$'cmd: $ARGV, at EOF: popping unclosed #if $ifExprs[$#ifexprs] from line $ifLines[$#ifLines]\n";
		  $xit=1;
		  &popif;
		}
	}

sub if	# (perlexpr) -> ok
	{ local($_)=&preproc(shift);
	  local($ok);

	  $ok=&eval($_);
	  if ($@)
		{ print STDERR "$'cmd: $ARGV, $.: syntax error in #if $_: $@\n";
	  	  $xit=1;
		  $ok=0;
		}

	  $ok;
	}

sub popif
	{ pop(@ifLines);
	  pop(@ifExprs);
	  pop(@ifPart);
	  pop(@ifActive);
	}

sub inc	# (from,lineno,filenames) -> void
	{ local($from,$lineno,$_)=@_;

	  for (split)
		{ if (!&inc1(&preproc($_)))
			{ print STDERR "$'cmd: $from, line $.: can't #include $_\n";
			}
		}
	}

sub inc1	# file -> void
	{ local($_)=@_;

	  if (/^<(.*)>$/)
		{ &stdinc($1);
		}
	  elsif (/^"(.*)"$/)
		{ &localinc($1) || &stdinc($1);
		}
	  else
	  { &localinc($_) || &stdinc($_);
	  }
	}

sub localinc	# (file) -> opened ok
	{ local($_)=@_;
	  local($F);

	  $_=&'catpath(&'dirname($from),$_) if !m:^/:;

	  return 0 unless defined($F=&'subopen('<',$_));

	  &preprocfile($_,$F);
	  !length || close($F);

	  1;
	}

sub stdinc	# (file) -> opened ok
	{ local($_)=@_;

	  if (m:^/:)
		{ &localinc($_);
		}
	  else
	  { local($path,$F);
	  
	    for $dir (@ipath)
		{ $path=&'catpath($dir,$_);
		  print STDERR "stdinc($path)\n";
		  if (defined($F=&'subopen('<',$path)))
			{ &preprocfile($path,$F);
			  close($F);
			  return 1;
			}
		}

	    0;
	  }
	}

1;
