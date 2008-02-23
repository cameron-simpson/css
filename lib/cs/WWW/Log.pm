#!/usr/bin/perl
#
# Parse standard WWW server log.
#	- Cameron Simpson <cs@zip.com.au> 27oct96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Date;
use cs::HTML;
use cs::HTTP;

package cs::WWW::Log;

sub new
{ my($class)=shift;
  local($_)=shift;

  return undef unless 
	# host    ?     ?       date         request     resp    size
	/^(\S+)\s+\S+\s+\S+\s+\[([^]]+)\]\s+"([^"]*)"\s+(\d+|\-)\s+(\d+|-)/io;

  my($host,$date,$req,$resp,$size)=($1,$2,$3,$4,$5);

  if ($size eq '-')	{ $size=0; }

  my($this)={ LINE => $_,
	      HOST => lc($host),
	      REQ  => $req,
	      RESP => $resp,
	      SIZE => $size,
	      DATE => $date,
	    };
  bless $this, $class;

  return undef if ! defined ($date=$this->ParseDate($date));

  $this->{TIME}=$date->{TIME};
  $this->{TM}=$date;

  if (defined ($req=$this->ParseRequest($req)))
	{ $this->{RQ}=$req;
	}

  $this;
}

sub ParseDate
{ my($this)=shift;
  local($_)=shift;

  return undef unless
	/(\d+)\/([a-z]{3})\/(\d+):(\d+):(\d+):(\d+)\s+\+(\d\d)(\d\d)/io;

  my($mday,$monnam,$year,$hh,$mm,$ss,$tzhh,$tzmm)
   =($1,$2,$3,$4,$5,$6,$7,$8);
  

  my($mnum)=cs::Date::mon2mnum($monnam);

  return undef if ! defined $mnum;

  my($tzmin)=$tzhh*60+$tzmm;

  if ($year >= 1900)	{ $year-=1900; }

  my($time)=::timelocal($ss,$mm,$hh,$mday,$mnum,$year);
  # -$tzmin*60;

  cs::Date::time2tm($time,1);
}

sub ParseRequest
	{ my($this)=shift;
	  local($_)=shift;

	  my(@rq)=map(cs::HTTP::unhexify($_),grep(length,split(/\s+/)));

	  return undef unless @rq;

	  my($rq)=uc(shift(@rq));

	  my($R)={ RQ => [ @rq ],
		   REQUEST => $rq,
		 };

	  if ($rq eq GET)
		{ $R->{URL}=shift(@rq);
		}

	  $R;
	}

1;
