#!/usr/local
#
# Edit the /etc/dumpdates file and its ilk.
#	- Cameron Simpson <cs@zip.com.au> 06dec96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Date;
use cs::Source;
use cs::Sink;

package cs::UNIX::DumpDates;

sub new
	{ my($class,$file)=@_;
	  my($this)={ PATH => $file };
	  bless $this, $class;

	  my($s);

	  return undef if ! $this->Load();

	  $this;
	}

sub Load
	{ my($this,$file)=@_;

	  $file=$this->{PATH} if ! defined $file;

	  my($s);

	  return 0 if ! defined ($s=new cs::Source PATH, $file);

	  local($_);
	  my($dev,$lev,$when,$gm,$tm);

	  $this->{LINES}=[];

	  LOAD:
	    while (defined($_=$s->GetLine()) && length)
		{ chomp;	s/\s+$//;

		  $l={ LINE => $_ };
		  push(@{$this->{LINES}},$l);

		  if (! m:^(/\S+)\s+(\d+)\s+(.*\S):)
			{ warn "$file: bad line [$_]";
			  next LOAD;
			}

		  ($dev,$lev,$when)=($1,$2,$3);

		  $l->{DEVICE}=$dev;
		  $l->{LEVEL}=$lev;

		  $ltime=cs::Date::ctime2gm($when);

		  if (! defined $ltime)
			{ warn "$file: can't parse date ($when)\n";
			}

		  $l->{LTIME}=$ltime;
		}

	  1;
	}

sub Save
	{ my($this,$file)=@_;

	  $file=$this->{PATH} if ! defined $file;

	  my($s);

	  return undef if ! defined ($s=new cs::Sink PATH, $file);

	  for (@{$this->{LINES}})
		{ if (defined $_->{LTIME})
			{ $s->Put($_->{DEVICE}, "\t",
				  $_->{LEVEL}, "\t",
				  cs::Date::tm2ctime(
					cs::Date::time2tm(
						$_->{LTIME},1)),
				  "\n");
			}
		  else
			{ $s->Put($_->{LINE},"\n");
			}
		}

	  1;
	}

sub Note
	{ my($this,$dev,$lev,$when)=@_;
	  $when=time if ! defined $when;
	  $lev=9     if ! defined $lev;

	  LINE:
	    for (@{$this->{LINES}})
		{ next LINE unless defined $_->{DEVICE} && defined $_->{LEVEL};
		  next LINE unless $_->{DEVICE} eq $dev && $_->{LEVEL} == $lev;
		  $_->{LTIME}=$when;
		}
	}
1;
