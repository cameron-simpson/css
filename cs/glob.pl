#!/usr/bin/perl
#
# Glob related routines.
# Defines
#	&glob(pattern) -> @files
#	%glob'bed{pattern} -> NULfile-list
#	%glob'ptn{pattern-part} -> regexp

require 'cs/dir.pl';

sub glob	# ptn -> @files
	{ local($_)=shift;
	  my(@globbed);

	  upd'nl("glob($_) ...");
	  if (defined $glob'bed{$_})
		{ @globbed=@{$glob'bed{$_}};
		  upd'nl("seen [$_], returning [@globbed]");
		}
	  else
	  { my($_orig)=$_;
	    my($sofar);

	    if (m:^~([^/]*):)
		{ my($user)=$1;
		  my($name,$passwd,$uid,$gid,$quota,$comment,$gcos,$dir,
			$shell) = getpwnam($user);

		  if (defined($dir))
			{ $_=$dir.$';
			}
		}

	    # get leading slashes
	    m:^/*:;
	    $sofar=$&; $_=$';

	    @globbed=&glob'_glob($sofar,split(m:/+:));
	    upd'nl("stashing glob($_orig) as [@globbed]");
	    $glob'bed{$_orig}=[ @globbed ];
	  }

	  @globbed;
	}

sub glob'_glob	# (prefix,@parts) -> @files
	{ my($sofar)=shift;
	  local($_);

	  SOFAR:
	    while (defined($_=shift)
	        && !/[[\]?*]/)
		# literal, append directly
		{ if (length($sofar) && $sofar !~ m:/$:)
			{ $sofar.='/';
			}
		  $sofar.=$_;
		}
	
	  if (! defined)
		# pure literal, return it
		{ return $sofar;
		}

	  # ok, $_ must be a pattern component
	  local($ptn);

	  upd'err("_glob: sofar=[$sofar], _=[$_], parts=[@_]\n");

	  if (defined $glob'ptn{$_})
	      # seen this before, extract regexp
	      { $ptn=$glob'ptn{$_};
	      }
	  else
	  { local($_orig)=$_;

	    # optimise for leading *
	    if (/^\*+/)	{ $ptn=''; $_=$'; }
	    else		{ $ptn='^'; }

	    while (length)
	      { # match [range]
		if (/^\[(!)?([^]-](-[^]-])?)+\]/)
		      { $ptn.='['
			     .(length($1) ? '^' : '')
			     .$2
			     .']';
		      }
		elsif (/^\*+/)
		      { $ptn.='.*';
		      }
		elsif (/^\?+/)
		      { $ptn.='.' x length($&);
		      }
		elsif (/^[^[*?]+/)
		      { $ptn.=$&;
		      }
		else
		{ print STDERR "$cmd: can't parse shell pattern at \"$_\"\n";
		  /.*/;
		  $ptn.=$&;
		}

		$_=$';
	      }

	    # optimise for trailing *
	    if ($ptn =~ /\.\*$/)	{ $ptn=$`; }
	    else			{ $ptn.='$'; }

	    $glob'ptn{$_orig}=$ptn;
	    # print STDERR "glob $_orig -> $ptn\n";
	  }

	  # collect matching entries from prefix directory
	  local(@matched)=grep(/$ptn/,&main'cacheddirents($sofar));

	  if ($#matched < $[)
	      # no entries; short circuit
	      { return ();
	      }

	  local(@globbed)=();

	  if (length($sofar) && $sofar !~ m:/$:)	{ $sofar.='/'; }

	  if ($#_ < $[)
		# no further parts, tack globs onto $sofar
		{ for (@matched)
			{ push(@globbed,$sofar.$_);
			}
		}
	  else
	  # more components; tack onto sofar and glob further
	  { for (@matched)
		{ push(@globbed,&glob'_glob($sofar.$_,@_));
		}
	  }

	  @globbed;
	}

undef %glob'bed,	# NUL separated globbed lists		( *.c -> a.c, b.c, ... )
	%glob'ptn;	# regexp matching glob component	( *.c -> \.c$ )

1;	# for require
