#!/usr/bin/perl
#
# Module for dealing with processes.
#	- Cameron Simpson <cs@zip.com.au>
#
# proc(pid) -> { proc details }
# sync([force]) -> void
#	Do another ps if force || out of date.
# dump	Dump process table.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Proc;

undef %cs::Proc::_nam2uid;
undef %cs::Proc::_uid2nam;

sub new
	{ my($class,$arch)=@_;
	  $arch=$ENV{ARCH} if ! defined $arch;

	  my($psarch,$pssub);

	  ($psarch=$arch) =~ tr/./_/;
	  $pssub="_ps_$psarch";

	  my(@ps)=&$pssub();

	  my($this)={ ARCH	=> $arch,
		      PROC	=> {},
		      TIME	=> time,
		    };

	  my($p);

	  for $p (@ps)
		{
		  $this->{PROC}->{$p->{PID}}
			=bless { CHILDREN => [],
				 PDATA    => $p,
				 TABLE    => $this,
			       }, $class;
		}

	  # parent-child hookups
	  for $p (map($this->{PROC}->{$_}, keys %{$this->{PROC}}))
		{
		  my($pid) =$p->{PDATA}->{PID};
		  my($ppid)=$p->{PDATA}->{PPID};

		  if (! exists $this->{PROC}->{$ppid})
			{ warn "$::cmd: no parent $ppid for $pid";
			  $this->{PROC}->{$ppid}
				=bless { CHILDREN => [],
					 PDATA    => { PID => $ppid,
						     },
					 TABLE    => $this,
				       }, $class;
			}

		  push(@{$this->{PROC}->{$ppid}->{CHILDREN}},$p);
		}

	  bless $this, $class;
	}

sub _ps_sun_sparc_solaris
	{
	  my(@ps)=();

	  if (! open(PS,"ps -A -o 'f uid pid ppid pri nice vsz rss wchan s tty time args' |"))
		{ warn "$::cmd: popen(ps): $!";
		}
	  else
	  {
	    local($_);

	    PS:
	      while (defined ($_=<PS>))
		{ chomp;
		  
		  #    FLAGS   UID     PID     PPID    PRI     NI      SIZE    RSS     WCHAN   STA     TTY     TIME    CMD
		  /^\s*(\d+)\s+(\w+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S)/
			|| next PS;

		  push(@ps,
		  {
		    MISC => {
			      FLAGS => $1,
			      PRI   => $5,
			    },
		    UID  => _uidof($2),
		    PID  => $3+0,
		    PPID => $4+0,
		    NICE => $6+0,
		    SIZE => $7+0,
		    RSS  => $8+0,
		    WCHAN=> $9,
		    STATE=> $10,
		    TTY  => $11,
		    TIME => _time2secs($12),
		    ARGV => [ _wordsof($13.$') ],
		  }
		  );
		}
	  }

	  @ps;
	}

sub _ps_linux_x86_linux
	{
	  my(@ps)=();

	  if (! open(PS,"ps -axlhwwww |"))
		{ warn "$::cmd: popen(ps): $!";
		}
	  else
	  {
	    local($_);

	    PS:
	      while (defined ($_=<PS>))
		{ chomp;
		  
		  #    FLAGS   UID     PID     PPID    PRI     NI      SIZE    RSS     WCHAN   STA     TTY     TIME    CMD
		  /^\s*(\d+)\s+(\w+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S)/
			|| next PS;

		  push(@ps,
		  {
		    MISC => {
			      FLAGS => $1,
			      PRI   => $5,
			    },
		    UID  => _uidof($2),
		    PID  => $3,
		    PPID => $4,
		    NICE => $6,
		    SIZE => $7,
		    RSS  => $8,
		    WCHAN=> $9,
		    STATE=> $10,
		    TTY  => $11,
		    TIME => _time2secs($12),
		    ARGV => _wordsof($13.$'),
		  }
		  );
		}
	  }

	  @ps;
	}

sub _uidof
	{ local($_)=@_;
	  return $_+0 if /^\d+$/;
	  _nam2uid($_);
	}

sub _uid2nam
	{ my $uid = shift(@_);

	  return $cs::Proc::_uid2nam[$uid] if defined $cs::Proc::_uid2nam[$uid];

	  my $nam;

	  return undef if ! defined ($nam=getpwuid($uid));

	  $cs::Proc::_uid2nam[$uid]=$nam;

	  return $nam;
	}

sub _nam2uid
	{ my $nam = shift(@_);

	  return $cs::Proc::_nam2uid{$nam} if defined $cs::Proc::_nam2uid{$nam};

	  my $uid;

	  return undef if ! defined ($uid=getpwnam($nam));

	  $cs::Proc::_nam2uid[$nam]=$uid;

	  return $uid;
	}

sub _time2secs
	{ local($_)=@_;

	  my($time);

	  if (/^(\d+)-(\d+):(\d\d):(\d\d)(\.\d+)?$/)
		{ $time=$1*3600*24+$2*3600+$3*60+$4;
		}
	  elsif (/^(\d+):(\d\d):(\d\d)(\.\d+)?$/)
		{ $time=$1*3600+$2*60+$3;
		}
	  elsif (/^(\d+):(\d\d(\.\d+)?)$/)
		{ $time=$1*60+$2;
		}
	  else	{ return $_;
		}

	  $time;
	}

sub _wordsof
	{ local($_)=@_;
	  grep(length,split(/\s+/));
	}

sub Parent
	{ my($this)=@_;
	  my($t)=$this->{TABLE};

	  $t->{PROC}->{$this->{PDATA}->{PPID}};
	}

sub Children
	{ @{shift->{CHILDREN}};
	}

1;
