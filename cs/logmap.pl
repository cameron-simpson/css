#!/usr/bin/perl
#
# Logging subsystem.
#	- Cameron Simpson <cs@zip.com.au>, 21jul94
#
# The environment variable $HOSTNAME is prefixed to 
# &logmap(mapfile)
#	Set name of file to consult for mapping info.
#	Default: $HOME/.logmap
# &chklogmap
#	Reload the map file if it's been changed.
# &loadlogmap($clearmap) -> ok
#	Reload the map file, first forgetting the old map
#	if $clearmap.
# &map($logical,$logspec) -> ok
#	Save a mapping $logical->$logspec, updating the map file.
# &logto(FILE,$logical) -> ok
#	Attach FILE to the log specified by the logspec associated with
#	$logical.
#	If the logspec commences with '>' or '|' it is passed directly to
#		open().
#	If the logspec matches ^{.*}$ then it is taken to be the body
#		of a subroutine to open a log. FILE (prefixed with the
#		caller's package name if necessary) is passed as the
#		parameter. The subroutine should return success or failure.
#		The subroutine runs in the logmap package.
#	Otherwise ">> logspec\0" is passed to open().
#

use POSIX;
require 'stat.pl';
require 'cs/date.pl';

package logmap;

$Debug=0;

&logmap("$'ENV{HOME}/.logmap");
undef %logmap;

sub logmap	# filename
	{ $logmap=shift;
	  undef $logmaptime;
	}

sub chklogmap	
	{ local(@stat,$reload,$now,$stattime);

	  $reload=0;
	  $now=time;
	  @stat=stat($logmap);
	  if (!@stat)
		{ print STDERR "$'cmd: stat($logmap): $!\n"
			if $! != POSIX->ENOENT;
		}
	  else	{ $stattime=$stat[$'ST_MTIME]; }

	  if (!defined($logmaptime))	{ $reload=1; }
	  elsif (!@stat)		{ $reload=0; }
	  elsif ($stattime > $logmaptime){ $reload=1; }
	  else				{ $reload=0; }

	  if ($reload)
		{ &loadlogmap;
		  $logmaptime=$stattime;
		  # print STDERR "logmaptime=$logmaptime\n";
		}
	}

sub loadlogmap	# clearmap -> ok
	{ local($clearmap)=shift;

	  if (!open(Logmap,"< $logmap\0"))
		{ print STDERR "$'cmd: open($logmap): $!\n"
			if $! != POSIX->ENOENT;
		  return 0;
		}

	  # print STDERR "reloading $logmap ...\n";
	  local($ok)=1;
	  local($_);

	  undef %logmap if $clearmap;

	  LOGMAP:
	    while (<Logmap>)
		{ chop;
		  s/^\s*(\#.*)?//;
		  next LOGMAP if /^$/;

		  if (/(\S+)\s+/)
			{ $logmap{$1}=$';
			}
		  else
		  { print STDERR "$'cmd: $logmap, line $.: bad format\n";
		    $ok=0;
		  }
		}

	  close(Logmap);

	  $ok;
	}

sub rewritelogmap	# void -> ok
	{ if (!open(Logmap,"> $logmap\0"))
		{ print STDERR "$'cmd: can't rewrite $logmap: $!\n";
		  return 0;
		}

	  local($_);

	  for (sort keys %logmap)
		{ next unless length $logmap{$_};
		  print Logmap $_, ' ', $logmap{$_}, "\n";
		}

	  close(logmap);
	  1;
	}

sub absspec
	{ local($_)=@_;

	  if (/^:/)	{ $_="$'\@$ENV{HOSTNAME}"; }
	  elsif (/\@$/)	{ $_="$`\@$ENV{HOSTNAME}"; }

	  $_;
	}

sub map	# (logical,logspec) -> ok
	{ if (!open(Logmap,">> $logmap\0"))
		{ print STDERR "$'cmd: can't append to $logmap: $!\n";
		  return 0;
		}

	  local($key,$logspec)=@_;

	  if ($logspec =~ m:console\.:)
		{ print STDERR "WARNING: $0: logmap'map($key -> $logspec)\n";
		}

	  &_map(&absspec($key),$logspec);

	  close(Logmap);

	  1;
	}
sub _map{ print Logmap $_[0], ' ', $_[1], "\n";
	  $logmap{$_[0]}=$_[1];
	}

sub logspec	# logspec -> logmap or undef
	{ local($_)=shift;
	  local($abs)=&absspec($_);

	  &chklogmap;

	  if (defined $logmap{$abs})
		{ $logmap{$abs};
		}
	  elsif (defined $logmap{$_})
		{ $logmap{$_};
		}
	  else
	  { undef;
	  }
	}

sub logto	# (FILE,logical-name) -> ok
	{ local($F,$l)=@_;

	  $F=caller(0)."'$F" unless $F =~ /'/;

	  $Debug && print STDERR "logto(F=$F, spec=$l): caller=[",join(' ',caller),"]\n";

	  &openspec($F,$l,0);
	}

sub openspec	# (FILE,spec,forinput) -> ok
	{ local($FILE,$logical,$forinput)=@_;
	  local($_);

	  $Debug && print STDERR "openspec(@_)\n";
	  $FILE=caller(0)."'$FILE" unless $FILE =~ /'/;

	  if ($logical eq '-')
		{ open($FILE,$forinput ? '<&STDIN' : '>&STDOUT');
		}
	  else
	  { if ($logical =~ /^[:\w]/)
		{ $_=&logspec($logical);
		  if (!length)
			{ print STDERR "$'cmd: no map for $logical\n";
			  return undef;
			}
		}
	    else
	    { $_=$logical;
	    }

	    if (/^[>|]/)
		{ if ($forinput)
			{ print STDERR "$'cmd: can't open \"$_\" for input\n";
			  return undef;
			}

		  open($FILE,$_);
		}
	    elsif (/^</ || /\|$/)
		{ if (!$forinput)
			{ print STDERR "$'cmd: can't open \"$_\" for output\n";
			  return undef;
			}

		  open($FILE,$_);
		}
	    else
	    { open($FILE,$forinput ? "< $_\0" : ">> $_\0");
	    }
	  }
	}

1;
