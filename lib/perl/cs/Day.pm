#!/usr/bin/perl
#
# Simple class working in days.
# Unlike cs::Date which takes a GMT for new(), this takes
# a daycode (yyyymmdd).
# Methods:
#	Code([emitlocaltime[,iso]]) -> yyyymmdd (or yyyy-mm-dd)
#	Prev([n]) -> new cs::Day (n days earlier)
#	Next([n]) -> new cs::Day (n days later)
# - Cameron Simpson <cs@zip.com.au> 04aug98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Date;

package cs::Day;

@cs::Day::ISA=(cs::Date);

sub new
{ my($class,$ymd,$givenlocaltime)=@_;
  $givenlocaltime=1 if ! defined $givenlocaltime;

  {my@c=caller;warn "OBSOLETE: new cs::Day($ymd,$givenlocaltime) from [@c]\n\tuse cs::DMY instead\n\t"};
  
  ## {my(@c)=caller;warn "new cs::Day $ymd from [@c]";}

  my $this = new cs::Date cs::Date::iso2gmt($ymd,$givenlocaltime);

  bless $this, $class;
}

sub newgmt
{ my($class,$gmt)=@_;
  $gmt=time if ! defined $gmt;
  {my@c=caller;warn "OBSOLETE: newgmt cs::Day($gmt) from [@c]\n\tuse cs::DMY(gmt) instead\n\t"};
  scalar(new cs::Day ((new cs::Date $gmt)->DayCode()));
}

sub Code
{ my($this)=shift;
  $this->DayCode(@_);
}

sub Prev
{ my($this,$n)=@_;
  $n=1 if ! defined $n;

  return $this->Next(-$n) if $n < 0;

  newgmt cs::Day $this->GMTime()-int($n)*24*3600;
}

sub Next
{ my($this,$n)=@_;
  $n=1 if ! defined $n;

  return $this->Prev(-$n) if $n < 0;

  my $oldgmt = $this->GMTime();
  my $newgmt = $oldgmt+int($n)*24*3600+2*3600;	# + 2 hours to beat DST transitions

##	  {my(@c)=caller;my($code)=$this->Code();
##	   if($code =~ /^19970[34]/)
##		{ warn "Next($code,$n) from [@c]\n\toldgmt=$oldgmt, newgmt=$newgmt";
##		}
##	  }

  newgmt cs::Day $newgmt;
}

1;
