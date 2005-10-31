#!/usr/bin/perl
#
# NFS-safe locks.
# Adapted from my lock script, which used to use mkdir.
# Now I use a umask current-umask+write-bits.
#	- Cameron Simpson <cs@zip.com.au> 26jun1999
#
# Seems not to work on Linux; maybe it tries to be too
# clever. Back to mkdir().			- cameron 27jun1999
#
# Add some timing uncertainty (based on various prime moduli of the pid)
# so that lots of lock calls started at once don't remain in synchrony
# so easily. (Observed in my foreach script.)	- cameron 25aug2000
#

=head1 NAME

cs::Lock - cross host NFS-safe locking

=head1 SYNOPSIS

use cs::Lock;

=head1 DESCRIPTION

This module obtains a lock on a resource
via an agreed name and lock directory.

The current implementation uses directories
for the lock objects.
The B<Put> method, if used,
then makes files in these directories.

=cut

use strict qw(vars);

use cs::Misc;
use cs::Pathname;
use Fcntl;

package cs::Lock;

sub new($$;$$);

# delay parameters
# with a bit of uncertainty to reduce racing
$cs::Lock::Delay=1+($$%5);
$cs::Lock::Dincr=1+($$%7);
# over 30secs, the NFS cache time
$cs::Lock::Dmax=45+$cs::Lock::Dincr*($$%3);

$cs::Lock::Whingeafter=5;	# 5 consecutive lock failures? start saying so

$cs::Lock::Lockdir=(length $ENV{LOCKDIR}
		   ? $ENV{LOCKDIR}
		   : -d cs::Misc::tmpDir()."/locks/."
		     ? cs::Misc::tmpDir()."/locks"
		     : "$ENV{HOME}/.locks"
		   );

=head1 GENERAL FUNCTIONS

=over 4

=item keypath(I<key>)

Return the pathname of the lock object to be obtained from
the I<key> supplied.

=cut

sub keypath($)
{ local($_)=@_;
  ##s/_/__/g; # to make au.com.zip.cs.Lockdir a bit simpler
  s://+:/:g;
  s:/$::;
  s:^/::;
  s:/:_:g;
  "$cs::Lock::Lockdir/$_";
}

=item check(I<key>)

Check if the lock specified by I<key> is taken.

=cut

sub check($)
{ my($key)=@_;
  ## warn "checking ".keypath($key);
  -e keypath($key);
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Lock (I<key>,I<maxtries>,I<silent>)

Obtain a lock on the resource specified by I<key>.
If I<maxtries> is greater than zero,
try to take the lock that many times before failure
(returning B<undef>).
Successive attempts are separated by an increasing delay,
up to a maximum.
If I<maxtries> equals zero, try forever.
If I<maxtries> is less than zero,
return a lock object anyway (so that the current lock parameters may be queried).
If not supplied,
I<maxtries> defaults to zero.
If supplied and true, I<silent> specifies that locks delayed for a
noticable amount of time are not reported to B<STDERR>.

=cut

sub new($$;$$)
{ my($class,$key,$maxtries,$silent)=@_;
  $maxtries=0 if ! defined $maxtries;
  $silent=0 if ! defined $silent;

  my $this;

  if ($maxtries == 1 || $maxtries < 0)
  # the real work
  { $this = bless { KEY => $key,
		    PATH => keypath($key),
		    SILENT => $silent,
		    TAKEN => 0,
		  }, $class;
    if ($this->_Take())
    { $this->{TAKEN}=1;
      return $this;
    }

    return $this if $maxtries < 0;

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

  my $delay = $cs::Lock::Delay;

  do {
	$this=new($class,$key,1);
	if (defined $this)
	{ warn "$::cmd: got lock on $path after $slept seconds\n"
		if $saidwait && ! $silent;
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
	    { $silent || warn "$::cmd: waiting for lock on $path ...\n";
	      $saidwait=1;
	    }
	  }
	}

	sleep($delay); $slept+=$delay;
	if ($delay < $cs::Lock::Dmax)
	{ $delay=$cs::Lock::Dincr; }
	$waits++;
     }
  while(1);
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub DESTROY
{ my($this)=@_;

  if ($this->Taken())
  {
    (system("/bin/rm -r '$this->{PATH}'")>>8) == 0
      || warn "$::cmd: pid $$: rm -r $this->{PATH}: $!\n";

    $this->{TAKEN}=0;
  }
}

=item Taken()

Return whether the lock was successfully obtained.
This can only return false if the object was acquired
with I<maxtries> less than zero and the lock already taken by someone else.

=cut

sub Taken($)
{ return $_[0]->{TAKEN} != 0;
}

sub _SetInfo($)
{ my($this)=@_;

  my $info = $this->Path()."/info";

  if (! open(INFO,"> $info\0"))
  { warn "$::cmd: rewrite $info: $!\n";
    return 0;
  }

  print INFO "$$ $ENV{HOSTNAME}\n";
  close(INFO);
}

sub _Take($)
{ my($this)=@_;

  my $path = $this->Path();
  my $lockdir = cs::Pathname::dirname($path);

  # ensure work area exists
  if (! -d "$lockdir/."
   && ! cs::Pathname::makedir($lockdir)
     )
  { warn "$::cmd: makedir($lockdir): $!\n";
    return undef;
  }

  if (! mkdir($path,0777))
  { ## warn "$::cmd: mkdir($path): $!";
    return undef;
  }

  $this->_SetInfo();

  1;
}

=item Path()

Return the pathname of the lock object.

=cut

sub Path($)
{ shift->{PATH};
}

=item Put(I<info>,I<base>)

Write the text I<info> to the record I<base>
in the lock object, followed by a newline.
If not specified, I<base> defaults to "B<info>".

=cut

sub Put($$;$)
{ my($this,$info,$base)=@_;
  $base='info' if ! defined $base;

  my $path = $this->Path();
  my $file = "$path/$base";
  if (! open(INFO,"> $file\0"))
  { warn "$::cmd: cs::Lock::Put -> $file: $!\n";
    return 0;
  }

  print INFO $info, "\n";
  close(INFO);
}

=item Get(I<base>)

Retrieve the text stored in the record I<base>
in the lock object.
If not specified, I<base> defaults to "B<info>".

=cut

sub Get($;$)
{ my($this,$base)=@_;
  $base='info' if ! defined $base;

  my $path = $this->Path();
  my $file = "$path/$base";
  if (! open(INFO,"< $file\0"))
  { warn "$::cmd: cs::Lock::Get <- $file: $!\n";
    return undef;
  }

  local($_);
  $_=join('',<INFO>);
  close(INFO);
  chomp;

  return $_;
}

=back

=head1 FILES

The lock directory is chosen as follows:
the directory named in the environment variable B<$LOCKDIR>,
or the subdirectory B<locks> of the directory returned by cs::Misc::tmpDir(3) if it exists,
or the directory B<$HOME/.locks>.

=head1 ENVIRONMENT

B<LOCKDIR> - the preferred directory for locks.

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
