#!/usr/bin/perl
#
# Date related functions.
#

package date;

# weekday names
@wday_names=('sun','mon','tue','wed','thu','fri','sat');
@Wday_names=('Sun','Mon','Tue','Wed','Thu','Fri','Sat');
@weekday_names=('sunday','monday','tuesday','wednesday','thursday','friday','saturday');
@Weekday_names=('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday');
undef %wday;
undef %weekday;
{ local($i);
  $i=0; for (@wday_names) { $wday{$_}=$i++; }
  $i=0; for (@weekday_names) { $weekday{$_}=$i++; }
}
$wday_ptn=join('|',@wday_names);
$weekday_ptn=join('|',@weekday_names);

# month names
@mon_names=('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec');
@Mon_names=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec');
@month_names=('january','february','march','april','may','june','july','august','september','october','november','december');
@Month_names=('January','February','March','April','May','June','July','August','September','October','November','December');
undef %mon;
undef %month;
{ local($i);
  $i=0; for (@mon_names) { $mon{$_}=$i++; }
  $i=0; for (@month_names) { $month{$_}=$i++; }
}
$mon_ptn=join('|',@mon_names);
$month_ptn=join('|',@month_names);

sub mon2mnum
	{ local($_)=@_; s/^(...).+/$1/; tr/A-Z/a-z/;
	  return undef if !defined($mon{$_});
	  $mon{$_};
	}

sub wday2dow
	{ local($_)=@_; s/^(...).+/$1/; tr/A-Z/a-z/;
	  return undef if !defined($wday{$_});
	  $wday{$_};
	}

sub time2dmy	# (time,uselocaltime) -> "MMmonYY"
	{ local($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($_[1] ? localtime($_[0]) : gmtime($_[0]));

	  sprintf("%02d%s%02d",$mday,$mon_names[$[+$mon],$year);
	}
sub timecode	# (time,uselocaltime) -> yyyymmddhhmmss
	{ local($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($_[1] ? localtime($_[0]) : gmtime($_[0]));

	  sprintf("%02d%02d%02d%02d%02d%02d",$year+1900,$mon+1,$mday,$hour,$min,$sec);
	}
sub gm2dmy	# (time) -> "MMmonYY"
	{ local($time)=shift;
	  &time2dmy($time,0);
	}
sub gm2ldmy	# (time) -> "MMmonYY"
	{ local($time)=@_;
	  &time2dmy($time,1);
	}

sub datestr	# (time,uselocaltime) -> "MMmonYY, hh:mm:ss"
	{ local($sec,$min,$hour,$mday,$mon,$year,$wday,$yday,$isdst)
		=($_[1] ? localtime($_[0]) : gmtime($_[0]));

	  sprintf("%02d%s%02d, %02d:%02d:%02d",
		  $mday,$mon_names[$[+$mon],$year,$hour,$min,$sec);
	}

sub timestr	# (time) -> "[[[days, ]hours, ]minutes, ]seconds"
	{ local($time)=$_[0];
	  local($str,$slop);
	
	  $str="";
	  if ($time >= 86400)
		{ $slop=$time%86400;
		  $time-=$slop;
		  $str.=($time/86400)." days, ";
		  $time=$slop;
		}

	  if ($time >= 3600)
		{ $slop=$time%3600;
		  $time-=$slop;
		  $str.=($time/3600)." hours, ";
		  $time=$slop;
		}
	  
	  if ($time >= 60)
		{ $slop=$time%60;
		  $time-=$slop;
		  $str.=($time/60)." minutes, ";
		  $time=$slop;
		}
	  
	  $str.$time." seconds";
	}

sub ctime2gm
	{ local($_)=@_;
	  local($time);

	  #     1:mon       2:mday  3:hh  4:mm  5:ss      6:tzone  7:   8:yy
	  if (/($mon_ptn)\s+(\d+)\s+(\d+):(\d\d):(\d\d)\s*(\S+)?\s+(19)?(\d\d)/io)
		{ local($mon,$mday,$hh,$mm,$ss,$tzone,$yy)
			=($1,$2,$3,$4,$5,$6,$8);

		  local($mnum)=&mon2mnum($mon);
		  return undef if !defined($mnum);

		  { package main;
		    use Time::Local;	# require 'timelocal.pl';
		    if ("@INC" !~ m:/home/cameron/etc/pl:)
			{ print STDERR "HOSTNAME=$ENV{HOSTNAME}, PERLLIB=$ENV{PERLLIB}\n";
			}
		    use cs::RFC822;
		  }

		  $time=&'timelocal($ss,$mm,$hh,$mday,$mnum,$yy)
		       +RFC822::tzone2minutes($tzone);
		}
	  else
	  { return undef;
	  }

	  $time;
	}

# recognise various time formats
#	- rfc822 date format
#	- unix ctime format
sub txt2gm
	{ local($_)=@_;

	  { package main; use cs::RFC822; }

	  local($time);

	  if (defined($time=cs::RFC822::date2gm($_)))
		{}
	  elsif (defined($time=&ctime2gm($_)))
		{}
	  else
	  { return undef;
	  }

	  $time;
	}

1;
