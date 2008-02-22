#!/usr/bin/perl
#
# Code to track who's logged in.
#	- Cameron Simpson <cs@zip.com.au> 14mar95
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

require Hier;

package cs::Who;

$cs::Who::PUTMP="/home/cameron/s/cs/wtmp/wtmp.$ENV{ARCH} -dx < /usr/adm/utmpx |";

$last=0;	# last ps
undef @who;
$synced=0;
$sync_delay=10;

sub sync
	{ my($force)=@_;

	  if ($force || ! $synced)
				{ &_sync; }
	  else
	  { if (time-$last >= $sync_delay)
				{ &_sync; }
	  }
	}
sub _sync
	{ &_who;
	  $last=time;
	  $synced=1;
	}

sub 

sub dump
	{ &sync();

	  for ($[..$#who)
		{ print STDERR "$_: ", &Hier::h2a(\$who[$_]), "\n";
		}
	}

sub _ps	{ my($cfg)=$config{$'ENV{ARCH}};

	  &_ps_ARCH($cfg->{PSCMD},$cfg->{PARSE});
	}

sub _ps_ARCH	# (pscmd,\&parse) -> ok
	{ my($pscmd,$parse)=@_;

	  if (! open(PS,"$pscmd |"))
		{ print STDERR "$0: warning: can't pipe from \"$pscmd\": $!\n";
		  return undef;
		}

	  local($_);

	  undef @ps;

	  $_=<PS>;	# headers
	  PS:
	    while (<PS>)
		{ chomp;
		  $proc=&$parse($_);
		  next PS if ! defined $proc;
		  $ps[$proc->{PID}]=$proc;
		}

	  close(PS);
	}

sub _fields2proc	# domain,\@fields,comd,@FIELDNAMES -> { FIELDNAME => value, ... }
	{ my($domain,$fref,$comd,@FIELDS)=@_;
	  my($FIELD,$value,$parse,%parsed);
	  my($proc)={};

	$proc->{COMMAND}=[ grep(length,split(/\s+/,$comd)) ];
	$Debug && print STDERR "COMMAND=[@{$proc->{COMMAND}}]\n";
	FIELD:
	  for $FIELD (@FIELDS)
	      { $value=shift @$fref;
		$parse="_parse_${domain}_${FIELD}";
		if (! defined &$parse)
		      { $proc{$FIELD}=$value;	# keep original
			$Debug && print STDERR "$FIELD=$value\n";
		      }
		else
		{ %parsed=&$parse($value);
		  $proc->{"_$FIELD"}=$value;	# keep original
		  ($Debug || $FIELD eq STIME) && print STDERR "_$FIELD=$value\n";
		  for (keys %parsed)		# save derived fields
		      { $proc->{$_}=$parsed{$_};
			($Debug || $_ eq START) && (print STDERR "\t$_=$parsed{$_}\n");
		      }
		}
	      }

	exit 0 if $timetodie;
	$proc;
      }

sub _parse_ps_elf
	{ local($_)=@_;
	  my(@fields,$comd);

	  #                  F       S         UID     PID     PPID    C       PRI       NI             P       SZ:   RSS     WCHAN        STIME                                    TTY            TIME       COMD
	  if (@fields = /^ *(\d+)\s+([A-Z])\s*(\w+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(\d+|[A-Z]+)\s+(\S+)\s+(\d+):(\d+)\s+([\da-f]*)\s+([A-Z][a-z][a-z] \d\d|\d\d?:\d\d:\d\d)\s+(\?|[\w\/]+)\s+(\d+:\d+)\s+/)
		{ $proc=&_fields2proc('elf',\@fields,$',
					    F,S,UID,PID,PPID,C,PRI,NI,P,SZ,RSS,
					    WCHAN,STIME,TTY,TIME);
		}
	  else
	  { print STDERR "$0: can't parse ps_elf: \"$_\"\n";
	    return undef;
	  }

	  return $proc;
	}

sub _numeric		{ $_[$[]+0; }
sub _parse_elf_F	{ (F,&_numeric); }
sub _parse_elf_PID	{ (PID,&_numeric); }
sub _parse_elf_PPID	{ (PPID,&_numeric); }
sub _parse_elf_PRI	{ (PRI,&_numeric); }
sub _parse_elf_SZ	{ (SZ,&_numeric); }
sub _parse_elf_RSS	{ (RSS,&_numeric); }
sub _parse_elf_TIME	{ local($_)=@_;

			  return (CPU,$1*60+$2 if /^(\d+):(\d+)$/);
			  return ();
			}
sub _parse_elf_STIME	{ local($_)=@_;
			  my($sec,$min,$hour,$mday,$mon,$year)=localtime(time);

			  { package main;
			    use Time::Local;
			  }

			  if (/^([A-Z][a-z][a-z])\s+(\d+)$/)
				{ my($mnam,$mday);

				  $mnam=$1; $mday=$2+0;
				  return () if ! defined $Mon{$mnam};

				  return (START,
					  &Time::Local::timelocal(0,0,0,$mday,
								  $Mon{$mnam},
								  $year));
				}

			  if (/^(\d+):(\d\d):(\d\d)$/)
				{ my($hour,$min,$sec)=($1,$2,$3);

				  my $when =
					  &Time::Local::timelocal($sec,$min,$hour,
								  $mday,
								  $mon,
								  $year);
				  print STDERR "STIME: [$_]: hour=$hour,min=$min,sec=$sec.  mday=$mday, mon=$mon, year=$year\n";
				  $timetodie=1;
				  return (START,$when);
				}

			  ();
			}


sub _parse_elf_UID
      { local($_)=@_;

	if (/^\d+$/)
	      { my $nam = &_uid2nam($_);
	      
		return (UID,$_) if ! defined $nam;

		return (UID,$_,USER,$nam);
	      }

	my $uid = &_nam2uid($_);

	return (USER,$_) if ! defined $uid;

	return (UID,$uid,USER,$_);
      }

sub _uid2nam
      { my $uid = shift(@_);

	return $uid2nam[$uid] if defined $uid2nam[$uid];

	my $nam;

	return undef if ! defined ($nam=getpwuid($uid));

	$uid2nam[$uid]=$nam;

	return $nam;
      }

sub _nam2uid
      { my $nam = shift(@_);

	return $nam2uid{$nam} if defined $nam2uid{$nam};

	my $uid;

	return undef if ! defined ($uid=getpwnam($nam));

	$nam2uid[$nam]=$uid;

	return $uid;
      }

1;
