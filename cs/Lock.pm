#!/usr/bin/perl
#
# NFS-safe locks.
# Adapted from my lock script, which used to use mkdir.
# Now I use a umask current-umask+write-bits.
#	- Cameron Simpson <cs@zip.com.au> 26jun99
#
# Seems not to work on Linux; maybe it tries to be too
# clever. Back to mkdir().	- cameron 27jun99
#

use strict qw(vars);

use cs::Misc;
use cs::Pathname;
use Fcntl;

package cs::Lock;

# delay parameters
$cs::Lock::Delay=5;
$cs::Lock::Dincr=5;
$cs::Lock::Dmax=30;

$cs::Lock::Whingeafter=5;	# 5 consecutive lock failures? start saying so

$cs::Lock::Lockdir=(length $ENV{LOCKDIR}
		   ? $ENV{LOCKDIR}
		   : -d cs::Misc::tmpDir()."/locks/."
		     ? cs::Misc::tmpDir()."/locks"
		     : "$ENV{HOME}/.locks"
		   );

sub new($$;$)
{ my($class,$key,$maxtries)=@_;
  $maxtries=0 if ! defined $maxtries;

  my $this;

  if ($maxtries == 1)
  # the real work
  { $this = bless { KEY => $key,
		    PATH => keypath($key),
		  }, $class;
    return $this if $this->Take();
    delete $this->{PATH};
    return undef;
  }

  # maxtries != 1
  # try several times
  my $waits = 0;
  my $isatty = -t STDERR;
  my $saidwait = 0;
  my $slept = 0;
  my $firstwait = 0;
  my $path = keypath($key);

  do {
	$this=new($class,$key,1);
	if (defined $this)
	{ warn "$::cmd: got lock on $path after $slept seconds\n"
		if $saidwait;
	  return $this;
	}
	## else {warn "this=$this, path=$path\n";}

	if ($maxtries > 0)
	{ return undef if --$maxtries == 0;
	}

	if ($isatty)
	{ if ($firstwait)
	  { $firstwait=0; }
	  else
	  { if (! $saidwait && $waits >= $cs::Lock::Whingeafter)
	    { warn "$::cmd: waiting for lock on $path ...\n";
	      $saidwait=1;
	    }
	  }
	}

	sleep($cs::Lock::Delay); $slept+=$cs::Lock::Delay;
	if ($cs::Lock::Delay < $cs::Lock::Dmax)
	{ $cs::Lock::Delay+=$cs::Lock::Dincr; }
	$waits++;
     }
  while(1);
}

sub DESTROY
{ my($this)=@_;
  ! exists $this->{PATH}
      || rmdir($this->{PATH})
      || warn "$::cmd: rmdir($this->{PATH}): $!\n";
}

sub keypath($)
{ local($_)=@_;
  s/_/__/g;
  s://+:/:g;
  s:/$::;
  s:^/::;
  s:/:_:g;
  "$cs::Lock::Lockdir/$_";
}

sub Take($)
{ my($this)=@_;

  my $lockdir = cs::Pathname::dirname($this->{PATH});

  # ensure work area exists
  if (! -d "$lockdir/."
   && ! cs::Pathname::makedir($lockdir)
     )
  { warn "$::cmd: makedir($lockdir): $!\n";
    return undef;
  }

  if (! mkdir($this->{PATH},0777))
  { ## warn "$::cmd: mkdir($this->{PATH}): $!";
    return undef;
  }

  1;
}

sub check($)
{ my($key)=@_;
  ## warn "checking ".keypath($key);
  -e keypath($key);
}

1;
