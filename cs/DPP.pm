#!/usr/bin/perl
#
# The smarts for the Dumb PreProcessor.
#	- Cameron Simpson, October 1993
#
# Modularised.	- cameron, 23aug98
#

use strict qw(vars);

use cs::Pathname;
use cs::Source;

package cs::DPP;

@cs::DPP::IncPath=('.');
$cs::DPP::Verbose=0;

sub new
	{ my($class,$src,$name)=@_;
	  $name=$src if ! defined $name;

	  if (! ref $src)
		{ my $s = cs::Source::open($src);
		  return undef if ! defined $s;
		  $src=$s;
		}

	  my($this)=bless { DS	=> $src,
			    NAME=> $name,
			    INC	=> [ @cs::DPP::IncPath ],
			    SYM	=> {},	# symbol table
			    OLD => [],	# pushed states
			    VERBOSE => $cs::DPP::Verbose,
			    STATE => { IFLINES	=> [],
				     },
			  }, $class;

	  $this;
	}

$before=''; undef $sub_before;
$after='';  undef $sub_after;

sub _SaveState
	{ my($this)=@_;
	  push(@{$this->{OLD}},{ DS => $this->{DS}, 
				 STATE => $this->{STATE},
				 NAME => $this->{NAME},
			       });
	}

sub _RestoreState
	{ my($this)=@_;

	  die "nothing to restore!" if ! @{$this->{OLD}};

	  my $old = pop(@{$this->{OLD}});

	  for my $key (keys %$old)
		{ $this->{$key}=$old->{$key};
		}
	}

# define a symbol
sub Define
	{ my($this,$sym,$defn)=@_;

	  $this->{VERBOSE} && warn "define $sym $defn\n";

	  $this->{SYM}->{$sym}=$defn;
	}

sub Undefine
	{ my($this,$sym)=@_;
	  delete $this->{SYM}->{$sym};
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

sub PreProc	# (text) -> expanded
	{ my($this)=shift;
	  local($_)=shift;

	  ## &set_before_after;
	  ## &before;

	  my $sym = $this->{SYM};

	  my $lhs;
	
	  for ($lhs=''; /{(\w+)}/; )
		{ if (! exists $sym->{$1})
			{ warn "$::cmd: $this->{NAME}: warning: can't replace {$1}, symbol not defined\n";
			  $this->Define($1,"{$1}");
			}

		  $lhs.=$`.$symbol{$1};
		  $_=$';
		}

	  $_=$lhs.$_;

	  ## &after;

	  $_;
	}

sub DoLine
	{ my($this)=shift;
	  local($_)=shift;

	  my $verbose = $this->{VERBOSE};
	  my $state   = $this->{STATE};

	  $verbose && warn "| $_";

	  s/\r?\n$//;

	  if (/^#\s*if\s+/)
		{ push(@{$state->{IFLINES}},$.);
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
