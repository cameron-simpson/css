#!/usr/bin/perl
#
# Date related functions.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Time::Local;
use cs::Misc;

package cs::Date;

# new cs::Date gmt
# new cs::Date (tm,givenlocaltime)
sub new
{ my($class,$gmt)=(shift,shift);
  $gmt=time   if ! defined $gmt;

  if (ref $gmt)
	# assume we were given a tm struct
	# convert back to gmt
	{
	  ## {my(@c)=caller;warn "new cs::Date ".cs::Hier::h2a($gmt,0)." from [@c]";}
	  $gmt=tm2gmt($gmt,@_);
	  if ($gmt == -1)
		{ ::need(cs::Hier);
		  die "range error on "
		     .cs::Hier::h2a($gmt,0);
		}
	}

  my($tm);

  $tm=gmt2tm($gmt,0);

  bless { GMT	=> $gmt,
	  TM	=> { GMT	=> gmt2tm($gmt,0),
		     LOCAL	=> gmt2tm($gmt,1),
		   },
	}, $class;
}

sub GMTime { shift->{GMT}; }

sub Tm($$)
	{ my($this,$emitlocaltime)=@_;
	  $emitlocaltime=1 if ! defined $emitlocaltime;
	  $this->{TM}->{$emitlocaltime ? LOCAL : GMT};
	}

sub TMField($$$)
	{ my($this,$field,$emitlocaltime)=@_;
	  $emitlocaltime=1 if ! defined $emitlocaltime;
	  $this->Tm($emitlocaltime)->{$field};
	}

sub Sec { my($this)=shift; $this->TMField(SS,@_); }
sub Min { my($this)=shift; $this->TMField(MM,@_); }
sub Hour{ my($this)=shift; $this->TMField(HH,@_); }
sub MDay{ my($this)=shift; $this->TMField(MDAY,@_); }
sub Mon { my($this)=shift; $this->TMField(MON, @_); }
sub WDay{ my($this)=shift; $this->TMField(WDAY,@_); }
sub YDay{ my($this)=shift; $this->TMField(YDAY,@_); }
sub Year{ my($this)=shift; $this->TMField(YEAR,@_); }
sub Yy  { my($this)=shift; $this->TMField(YY,  @_); }

sub DayCode
{ my($this)=shift;
  gmt2yyyymmdd($this->GMTime(),@_);
}

# cs::Date::weekday names
@cs::Date::wday_names=('sun','mon','tue','wed','thu','fri','sat');
@cs::Date::Wday_names=('Sun','Mon','Tue','Wed','Thu','Fri','Sat');
@cs::Date::weekday_names=('sunday','monday','tuesday','wednesday','thursday','friday','saturday');
@cs::Date::weekday_names=('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday');
undef %cs::Date::wday;
undef %cs::Date::weekday;
{ my($i);
  $i=0; for (@cs::Date::wday_names) { $cs::Date::wday{$_}=$i++; }
  $i=0; for (@cs::Date::weekday_names) { $cs::Date::weekday{$_}=$i++; }
}
$cs::Date::wday_ptn=join('|',@cs::Date::wday_names);
$cs::Date::weekday_ptn=join('|',@cs::Date::weekday_names);

# month names
@cs::Date::mon_names=('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec');
@cs::Date::Mon_names=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec');
@cs::Date::month_names=('january','february','march','april','may','june','july','august','september','october','november','december');
@cs::Date::month_names=('January','February','March','April','May','June','July','August','September','October','November','December');
{ my($i);
  $i=0; for (@cs::Date::mon_names) { $cs::Date::mon{$_}=$i++; }
  $i=0; for (@cs::Date::month_names) { $cs::Date::month{$_}=$i++; }
}
$cs::Date::mon_ptn=join('|',@cs::Date::mon_names);
$cs::Date::month_ptn=join('|',@cs::Date::month_names);

# returns 0..11 (warning: tm wants 1..12)
sub mon2mnum
	{ local($_)=@_;
	  s/^(...).+/$1/; tr/A-Z/a-z/;
	  return undef if ! exists $cs::Date::mon{$_};
	  $cs::Date::mon{$_};
	}

sub mnum2mon
	{ my($mnum)=@_;
	  die "mnum2mon($mnum)" if $mnum < 1 || $mnum > 12;
	  $cs::Date::Mon_names[$mnum-1];
	}

sub wday2dow
	{ local($_)=@_; s/^(...).+/$1/; tr/A-Z/a-z/;
	  return undef if !defined($cs::Date::wday{$_});
	  $cs::Date::wday{$_};
	}

sub tm2gmt($$)
{ my($tm,$givenlocaltime)=@_;

  if ($tm->{YEAR} < 1970)
  { my(@c)=caller;
    warn "tm2gmt: YEAR=$tm->{YEAR}, adding 1900 (huh?) from [@c]";
    $tm->{YEAR}+=1900;
  }
  my(@p)=($tm->{SS},$tm->{MM},$tm->{HH},
	  $tm->{MDAY},$tm->{MON}-1,$tm->{YEAR}-1900);

  ## warn "p=[@p]";
  $givenlocaltime
	? Time::Local::timelocal(@p)
	: Time::Local::timegm(@p);
}

sub gmt2tm($$)
	{ my($gmtime,$emitlocaltime)=@_;

	  my($t_ss,$t_mm,$t_hh,$t_mday,$t_mon,$t_year,$t_wday,$t_yday)
	   =($emitlocaltime ? localtime($gmtime) : gmtime($gmtime));

	  return { TIME => $gmtime,
		   HH => $t_hh, MM => $t_mm, SS => $t_ss,
		   MDAY => $t_mday, MON => $t_mon+1, YEAR => 1900+$t_year,
		   WDAY => $t_wday, YY => $t_year%100,
		   YDAY => $t_yday,
		 };
	}

sub HumanDate
{ my($this)=shift;
  humanDate($this->GMTime(),@_);
}
sub humanDate($$)
{ my($tm)=gmt2tm($_[0],$_[1]);

  # warn "tm=".cs::Hier::h2a($tm,0);
  # warn "cs::Date::Mon_names=".cs::Hier::h2a(\@cs::Date::Mon_names,0);

  my($hd)=$tm->{MDAY}.' '.$cs::Date::Mon_names[$tm->{MON}-1].' '.$tm->{YEAR};
  $hd;
}

sub gmt2dmy($$)	# (gmtime,emitlocaltime) -> "MMmonYY"
	{ my($gmt,$emitlocaltime)=@_;

	  my($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($emitlocaltime ? localtime($gmt) : gmtime($gmt));

	  sprintf("%02d%s%02d",$mday,$cs::Date::mon_names[$[+$mon],$year);
	}

sub dmy2gmt($$$$)
{ my($mday,$mon,$year,$givenlocaltime)=@_;
  if (! defined $givenlocaltime)
	{ my(@c)=caller;
	  warn "no givenLocalTime, assuming ==1, from [@c]";
	}
  $givenlocaltime=1 if ! defined $givenlocaltime;

  if ($mon < 1 || $mon > 12)
	{ my(@c)=caller;
	  warn "dmy2gmt(@_): mon=$mon from [@c]";
	}

  if ($year < 1970)
  { my(@c)=caller;
    warn "dmy2gmtyear < 1970 (== $year) from [@c]";
    $year+=1900;
  }

  tm2gmt({ HH => 0, MM => 0, SS => 0,
	   MDAY => $mday, MON => $mon, YEAR => $year,
	 }, $givenlocaltime);
}

sub timecode($$) # (gmtime,emitlocaltime) -> yyyymmddhhmmss
	{ my($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($_[1] ? localtime($_[0]) : gmtime($_[0]));

	  sprintf("%02d%02d%02d%02d%02d%02d",$year+1900,$mon+1,$mday,$hour,$min,$sec);
	}

sub datestr($$)	# (gmtime,emitlocaltime) -> "MMmonYY, hh:mm:ss"
	{ my($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($_[1] ? localtime($_[0]) : gmtime($_[0]));

	  sprintf("%02d%s%02d, %02d:%02d:%02d",
		  $mday,$cs::Date::mon_names[$[+$mon],$year,$hour,$min,$sec);
	}

sub timestr	# (time) -> "[[[days, ]hours, ]minutes, ]seconds"
	{ my($time)=@_;

	  my @str;
	  my $slop;

	  $time=int($time+0.5);
	  if ($time >= 86400)
		{ $slop=$time%86400;
		  $time-=$slop;
		  push(@str,($time/86400)." days");
		  $time=$slop;
		}

	  if ($time >= 3600)
		{ $slop=$time%3600;
		  $time-=$slop;
		  push(@str,($time/3600)." hours");
		  $time=$slop;
		}
	  
	  if ($time >= 60)
		{ $slop=$time%60;
		  $time-=$slop;
		  push(@str,($time/60)." minutes");
		  $time=$slop;
		}

	  if ($time > 0)
		{ push(@str,"$time seconds");
		}

	  join(", ",@str);
	}

sub ctime2gm
	{ local($_)=@_;
	  my($time);

	  #     1:mon       2:mday  3:hh  4:mm  5:ss      6:tzone  7:   8:yy
	  if (/($cs::Date::mon_ptn)\s+(\d+)\s+(\d+):(\d\d):(\d\d)\s*(\S+)?\s+(19)?(\d\d)/io)
		{ my($mon,$mday,$hh,$mm,$ss,$tzone,$yy)
			=($1,$2,$3,$4,$5,$6,$8);

		  $tzone='+0000' if ! defined $tzone;

		  my($mnum)=mon2mnum($mon);	# 0..11
		  return undef if !defined($mnum);

		  { package main;
		    need(cs::RFC822);
		  }

		  $time=::timelocal($ss,$mm,$hh,$mday,$mnum,$yy)
		       +cs::RFC822::tzone2minutes($tzone);
		}
	  else
	  { return undef;
	  }

	  $time;
	}

sub tm2ctime
	{ my($tm)=shift;

	  sprintf("%3s %3s %2d %02d:%02d:%02d %4d",
		$cs::Date::Wday_names[$tm->{cs::Date::WDAY}],
		$cs::Date::Mon_names[$tm->{MON}-1],
		$tm->{MDAY},
		$tm->{HH},$tm->{MM},$tm->{SS},
		$tm->{YEAR});
	}

# time,givenlocaltime -> gmt
sub yyyymmdd2gmt($$)
{ local($_)=shift;
  my($givenlocaltime)=shift;

  if (! /^(\d\d\d\d)-?(\d\d)-?(\d\d)$/)
	{ my(@c)=caller;
	  warn "yyyymmdd2gmt($_) from [@c]";
	  return undef;
	}

  my($year,$mon,$mday)=($1+0,$2+0,$3+0);

  tm2gmt({ SS=>0, MM=>0, HH=>0,
	   MDAY=>$mday, MON=>$mon,
	   YEAR=>$year },
	  $givenlocaltime);
}

sub gmt2yyyymmdd($$;$)
{
  my($when,$emitlocaltime,$iso)=@_;
  $when=time if ! defined $when;
  $emitlocaltime=1 if ! defined $emitlocaltime;
  $iso=1 if ! defined $iso;

  ## warn "gmt2yyyymmdd(when=$when,emitlocal=$emitlocaltime,iso=$iso)\n";
  my($tm)=(new cs::Date $when)->Tm($emitlocaltime);

  sprintf($iso ? "%04d-%02d-%02d" : "%04d%02d%02d",
	  $tm->{YEAR},$tm->{MON},$tm->{MDAY});
}

# recognise various time formats
#	- rfc822 date format
#	- unix ctime format
sub txt2gm
	{ local($_)=@_;

	  ::need(cs::RFC822);

	  my $time;

	  if (defined($time=cs::RFC822::date2gm($_)))
		{}
	  # non-kosher, but I've seen it in email
	  # dow mon mday hh:mm:ss year tz
	  elsif (/^($cs::Date::wday_ptn)\s+($cs::Date::mon_ptn)\s+(\d+)\s+(\d\d?):(\d\d):(\d\d)\s+(\d{4})\s+([-+]?\d{4}|[a-z]+)/)
		{ my($mon,$dom,$hh,$mm,$ss,$yr,$offset)
			=($2,$3,$4,$5,$6,$7,$8);

		  my($mnum);
		  return undef unless defined($mnum=cs::Date::mon2mnum($mon));

		  if (length($yr) == 2)	{ $yr+=1900; }

		  $time=dmy2gmt($dom,$mnum+1,$yr,0)
		       +($hh*60+$mm)*60+$ss
		       -cs::RFC822::tzone2minutes($offset);
		}
	  elsif (defined($time=ctime2gm($_)))
		{}
	  elsif (defined($time=yyyymmdd2gmt($_,1)))
		{}
	  else
	  { warn "$_ doesn't look like a Date: line";
	    return undef;
	  }

	  $time;
	}

1;
