#!/usr/bin/perl -w
#
# Usage: ring [-a] [-f file]... patterns...
#
# Look up phone lists. Each entry has a non-blank in column one.
#	- Cameron Simpson
#

use POSIX;

($cmd=$0) =~ s:.*/::;
$usage="Usage: $cmd [-alLvV] [+l] [-f phonelist]... patterns...
	-a	Search all phonelists.
	-l	List names of relevant files.
	+l	List relevant entries (default).
	-L	Prefix matches with filenames.
	-p	Use PGP. (Decode encrypted phonelists.)
	+v	Not verbose (default).
	-v	Verbose.
	-V	Very verbose.
	-f	Explicitly specify phonelists.
	patterns Regular expressions to locate.
";

$telnos=(defined($ENV{TELNOS})
	? $ENV{TELNOS}
	: "$ENV{HOME}/.phonelist:/usr/local/misc/phonedir"
	);

$badopts=0;
$verbose=0;
$VERBOSE=0;
$listnames=0;
$shownames=0;
$usepgp=0;
@phonelists=();

OPTION:
while (@ARGV)
{ $_=shift;
  if (! /^-./)	{ unshift(@ARGV,$_); last OPTION; }

  last OPTION if ($_ eq '--');

  if ($_ eq '-l')	{ $listnames=1; }
  elsif ($_ eq '+l')	{ $listnames=0; }
  elsif ($_ eq '-L')	{ $shownames=1; $listnames=0; }
  elsif ($_ eq '+v')	{ $verbose=0; $VERBOSE=0; }
  elsif ($_ eq '-p')	{ $usepgp=1; }
  elsif ($_ eq '-v')	{ $verbose=1; }
  elsif ($_ eq '-V')	{ $verbose=1; $VERBOSE=1; }
  elsif ($_ eq '-f')	{ push(@phonelists,
				grep(length,split(/:/,shift)));
			}
  else			{ warn "$cmd: $_: unrecognised option\n";
			  $badopts=1;
			}
}

if (! @phonelists)
{ @phonelists=();
  for (split(/:/,$telnos))
  { unshift(@phonelists,$_) if -e $_;
  }

  warn "phonelists=[@phonelists]";
}

if (! @ARGV)
{ warn "$cmd: missing patterns\n";
  $badopts=1;
}

if ($usepgp && ! length($ENV{PGPPASS}))
{ warn "$cmd: asked for PGP but no \$PGPPASS set\n";
  $badopts=1;
}

die $usage if $badopts;

$ringptn=join('|',@ARGV);

for (@phonelists)
{ phone($_);
}

sub phone
{ local($_)=@_;

  $verbose && warn "$_ ...\n";

  if ($_ eq '-')
  { phonefile($_);
  }
  elsif (-d $_)
  { if (opendir(DIR,$_))
    { local(@entries)=readdir(DIR);
      close(DIR);

      for $e (@entries)
      { next if $e =~ /^\./;
	&phone("$_/$e");
      }
    }
    else
    { if ($! != POSIX->EACCES)
      { warn "$cmd: can't opendir($_): $!\n";
      }
    }
  }
  else
  { phonefile($_);
  }
}

sub phonefile
{ local($file)=@_;

  if ($file eq '-'
	? open(PHONES,'<&STDIN')
	: $file =~ /\.Z$/
	  ? open(PHONES,"zcat '$file' 2>/dev/null |")
	  : $file =~ /\.pgp$/
	    ? $usepgp
	      && length($ENV{PGPPASS})
	      ? open(PHONES,"pgp -fd <'$file' |")
	      : open(PHONES,"< /dev/null\0")
	    : open(PHONES,"< $file\0"))
  {}
  else
  { if ($! != POSIX->EACCES)
    { warn "$cmd: can't open $file: $!\n";
    }

    return;
  }

  local($context,$first,$entry)=($file,1);
  undef $entry;

  while (defined($_=<PHONES>))
  {
    if (/^\S/)
    { checkit($file,$entry) if defined $entry;
      $entry=$_;
    }
    else
    { $entry.=$_;
    }
  }

  close(PHONES);

  checkit($file,$entry) if defined $entry;
}

sub checkit
{ local($file,$_)=@_;

  if ($listnames && $named{$file})
  {}
  elsif (/$ringptn/oi)
  { if ($listnames)
    { print $file, "\n";
      $named{$file}=1;
    }
    else
    { if ($first)
      { ($verbose || $shownames) && print $context, ":\n";
	$first=0;
      }

      print;
    }
  }
}
