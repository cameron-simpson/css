#!/usr/bin/perl
#
# Look up phone lists. Each entry has a non-blank in column one.
#	- Cameron Simpson <cs@zip.com.au>
#

use POSIX;
use Fcntl;

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
	: "$ENV{HOME}/.phonelist"
	);

$badopts=0;
$verbose=0;
$VERBOSE=0;
$listnames=0;
$shownames=0;
$usepgp=0;
@phonelists=();

$::_GPG2=( length($ENV{GPG_AGENT_INFO}) && system("have-gpg-agent") == 0 );
##warn "GPG2=$::GPG2";

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
  elsif (-d "$_/.")
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

sub readphrase($)
{ my($prompt)=@_;

  return undef if ! -t STDIN || ! -t STDERR;
  system("stty -echo");
  print STDERR "$prompt: ";
  my $phrase = <STDIN>;
  system("stty echo");
  return undef if ! defined $phrase;
  print STDERR "\n";
  chomp($phrase);
  return undef if ! length $phrase;
  return $phrase;
}

sub getgpgphrase()
{ if (! $::_askedForGPG)
  { $::_GPGPhrase=readphrase("Enter GPG pass phrase");
    $::_askedForGPG=1;
  }

  return $::_GPGPhrase;
}

# return gpg command with fd for pass phrase
# phrase stored in handle GPGPHRASEFD - close this after use
sub gpgwith($)
{ my($phrase)=@_;
  
  open(GPGPHRASEFD, "+>", undef) || die "$0: can't make temp file for pass phrase: $!";
  my $oldout = select(GPGPHRASEFD); $|=1;
  select($oldout);
  print GPGPHRASEFD "$phrase\n" || die "$0: can't write passphrase to temp file: $!";
  seek(GPGPHRASEFD,0,0) || die "$0: rewind of GPGPHRASEFD fails: $!";
  sysseek(GPGPHRASEFD,0,0) || die "$0: rewind of GPGPHRASEFD fails: $!";
  my $tmpfd = fileno(GPGPHRASEFD);
  die "$0: fileno(GPGPHRASEFD) returns undef! \$! = $!" if ! defined $tmpfd;
  ##fcntl(GPGPHRASEFD, Fcntl::F_SETFL(), 0) || die "$0: can't clear close-on-exec for GPGPHRASEFD: $!";
  if ($tmpfd > $^F)
  { warn "$cmd: raising \$^F to $tmpfd and retrying\n" if $^F > 2;
    $^F = $tmpfd;
    close(GPGPHRASEFD);
    return gpgwith($phrase);
  }

  return "gpg --batch -q --passphrase-fd $tmpfd";
}

sub phonefile
{ local($file)=@_;

  if ($file =~ /\.gpg$/)
  {
    my $gpgcmd;

    if ($::_GPG2)
    { $gpgcmd = "gpg2 -q --use-agent";
    }
    else
    {
      my $phr = getgpgphrase();
      if (! defined $phr)
      { warn "$cmd: skipping $file\n";
	return;
      }

      $gpgcmd = gpgwith($phr);
    }

    if (! open(PHONES, " set -x; exec $gpgcmd --decrypt <'$file' |"))
    { if ($! != POSIX->EACCES)
      { warn "$cmd: pgp -fd <$file: $!\n";
      }
      close(GPGPHRASEFD);
      return;
    }
  }
  elsif ($file =~ /\.pgp$/)
  { if (! $usepgp)
    { return;
    }

    if (! open(PHONES,"pgp -fd <'$file' |"))
    { if ($! != POSIX->EACCES)
      { warn "$cmd: pgp -fd <$file: $!\n";
      }
      return;
    }
  }
  elsif ($usepgp)
  { return;
  }
  elsif ($file eq '-'
	? open(PHONES,'<&STDIN')
	: $file =~ /\.Z$/
	  ? open(PHONES,"zcat '$file' 2>/dev/null |")
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
