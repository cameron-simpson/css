#!/usr/bin/perl

$MLALIAS='mlalias';	# real command

($cmd=$0) =~ s:.*/::;
$usage="Usage: $cmd [-C] alias [-{A|D} owner]... [-c comment] \\
		[-f {open|closed|public|private}]... [-{a|d|s} addresses...]
       $cmd -R alias
       $cmd -m alias addresses...
       $cmd -mg [alias]

       The options -e, -r, -T, -v, and -x may also be used with any form.

	-A owner	Add owner of alias.
	-C		Create new alias.
	-D owner	Delete owner of alias.
	-R		Remove alias.
	-T		Protocol trace.
	-a addresses	Add addresses to alias.
	-c comment	Add comment to alias.
	-d addresses	Delete addresses from alias.
	-e		Expand alias contents.
	-f flag		Set flag on alias.
	-l		List all aliases.
	-m alias address Check if address is mentioned by alias.
	-mg [alias] address Check if address is mentioned by any alias.
	-q		Quiet.
	-r		Raw; prints machine parsable form.
	-s addresses	Set address list to addresses.
	-v		Verbose.
	-x		Print actual mlalias command executed.
";

$badopts=0;
@mlFlags=();
@addOwner=();
@delOwner=();
@flags=();
$f_create=0;
$f_remove=0;
$f_list=0;
$f_mentioned=0;
$f_gmentioned=0;
$f_add=0;
$f_del=0;
$f_set=0;
$xflag=0;
undef $mode, $alias, $comment;

ARGV:
  while (defined($_=shift))
	{ last ARGV if $_ eq '--';

	  if (!/^-./)		{ if (!defined($alias))
					{ $alias=$_; }
				  else
				  { unshift(@ARGV,$_);
				    last ARGV;
				  }
				}
	  elsif ($_ eq '-x')	{ $xflag=1; }
	  elsif ($_ eq '-C')	{ $f_create=1; }
	  elsif ($_ eq '-R')	{ $f_remove=1; }
	  elsif ($_ eq '-A')	{ push(@addOwner,shift); }
	  elsif ($_ eq '-D')	{ push(@delOwner,shift); }
	  elsif (/^-[eqrvT]$/)	{ push(@mlFlags,$_); }
	  elsif ($_ eq '-a')	{ $f_add=1;
				  push(@mlFlags,$_);
				}
	  elsif ($_ eq '-d')	{ $f_del=1;
				  push(@mlFlags,$_);
				}
	  elsif ($_ eq '-s')	{ $f_set=1;
				  push(@mlFlags,$_);
				}
	  elsif ($_ eq '-f')	{ push(@flags,shift); }
	  elsif ($_ eq '-c')	{ $comment=shift; }
	  elsif ($_ eq '-m')	{ $f_mentioned=1; }
	  elsif ($_ eq '-mg')	{ $f_gmentioned=1; }
	  elsif ($_ eq '-l')	{ $f_list=1; }
	  else
	  { print STDERR "$cmd: $_: unrecognised option\n";
	    $badopts=1;
	  }
	}

# sanity checks
if ($f_create
   +$f_remove
   +$f_list
   +$f_mentioned
   +$f_gmentioned
   > 1
   )
	{ print STDERR "$cmd: -C,-R,-l,-m and -mg are mutually exclusive\n";
	  $badopts=1;
	}
elsif ($f_create)
	{}
elsif ($f_remove || $f_list || $f_gmentioned)
	{ if ($f_add || $f_del || $f_set || @addOwner || @delOwner || defined $comment)
		{ print STDERR "$cmd: can't use -a,-d,-s,-A,-D or -c with -l or -R\n";
		  $badopts=1;
		}
	}
elsif ($f_mentioned)
	{}

if ($f_set+$f_add+$f_del > 1)
	{ print STDERR "$cmd: -a, -d and -s are mutually exclusive\n";
	  $badopts=1;
	}
elsif ($f_create && !($f_set || $f_add || $f_del))
	{ $f_set=1;
	  push(@mlFlags,'-s');
	}

if (defined($alias))
	{ if ($f_list)
		{ print STDERR "$cmd: can't supply an alias with -l\n";
		  $badopts=1;
		}
	}
else
{ if (!$f_list && !$f_gmentioned)
	{ print STDERR "$cmd: missing alias name\n";
	  $badopts=1;
	}
}

die $usage if $badopts;

@addresses=@ARGV;

if ($f_create)		{ unshift(@flags,'public');
			  unshift(@mlFlags,'-C',$alias);
			}
elsif ($f_remove)	{ unshift(@mlFlags,'-R',$alias); }
elsif ($f_list)		{ unshift(@mlFlags,'-l'); }
elsif ($f_mentioned)	{ unshift(@mlFlags,'-m',$alias); }
elsif ($f_gmentioned)	{ unshift(@mlFlags,'-mg',$alias); }
else			{ unshift(@mlFlags,$alias); }

if (defined($comment))
	{ push(@mlFlags,'-c',$comment);
	} 

for (@flags)
	{ push(@mlFlags,'-f',$_);
	}

for (@delOwner)
	{ push(@mlFlags,'-D',$_);
	}

for (@addOwner)
	{ push(@mlFlags,'-A',$_);
	}

@exec=($MLALIAS,@mlFlags,@addresses);
print STDERR "+ @exec\n" if $xflag;
$xit=system(@exec);

$xit>>=8;
if ($xit > 16)	{ print STDERR "$cmd: adjusted $MLALIAS exit status ($xit) to 0\n";
		  $xit=0;
		}

if ($xit == 0)
	# it went ok, announce modifications
	{ if ($f_set
	   || ( ($f_add || $f_del) && $#addresses >= $[ )
	     )
		{ die "$cmd: open pipe: $!" unless defined($pid=open(MAIL,"|-"));

		  if ($pid == 0)
			# child
			{ @mail=('mail',
				 '-s',
				 "change to $alias mail alias",
				 $alias,
				 @addresses);
			  print STDERR "+ @mail\n" if $xflag;
			  exec @mail;
			  print STDERR "$cmd: exec(@mail) fails: $!\n";
			  exit 1;
			}
		  
		  if ($f_set)
			{ print MAIL "The $alias mail alias has been set to:\n\n";
			}
		  elsif ($f_add)
			{ print MAIL "The follow addresses have been added to \"$alias\":\n\n";
			}
		  else
		  { print MAIL "The follow addresses have been deleted from \"$alias\":\n\n";
		  }

		  if (!$f_set)
		  	{ for (@addresses)
				{ print MAIL "\t", $_, "\n";
				}

		  	  print MAIL "\nThe current alias is now:\n\n";
			}

		  if (open(MLALIAS,"$MLALIAS '$alias'|"))
			{ print MAIL <MLALIAS>;
			  close(MLALIAS);
			}
		  else
		  { print STDERR "$cmd: can't pipe from $MLALIAS: $!\n";
		    $xit=1;
		  }

		  close(MAIL);
		}
	}

exit $xit;
