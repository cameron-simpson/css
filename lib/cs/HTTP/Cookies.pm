#!/usr/bin/perl
#
# Do HTTP-related cookie stuff.
#	- Cameron Simpson <cs@zip.com.au> 12mar98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HTTP;
use cs::Persist;

package cs::HTTP::Cookies;

@cs::HTTP::Auth::ISA=qw();

sub finish
{ cs::Persist::finish();
}

sub new
{ my($class,$db,$rw)=@_;
  $db="$ENV{HOME}/.http-cookie-db" if ! defined $db;
  $rw=0 if ! defined $rw;

  if (! ref $db)
	{ return undef if ! defined ($db=cs::Persist::db($db,$rw))
	}

  bless { DB => $db,
	}, $class;
}

# hdr -> (cname,cvalue,paramhash)
sub ParseCookieHdr
{ my($this,$chdr)=@_;

  my($h,$chdr2)=cs::HTTP::parseAttrs($chdr,1);
  my($cname,$cval)=%$h;

  return undef if ! defined $cname;

  $h=cs::HTTP::parseAttrs($chdr2);

  ( $cname, $cval, $h );
}

sub normHost
{ local($_)=@_;
  s/:80$//;
  lc($_);
}

# Look up cookies for URL..
# url -> { USERID => userid, PASSWORD => password }
sub GetCookies
{ my($this,$url)=@_;

  $url=new cs::URL $url if ! ref $url;
  return undef if ! exists $url->{HOST}
	       || ! length $url->{HOST};

  my($db)=$this->{DB};

  my($hostkey)=$url->{HOST};
  $hostkey.=":$url->{PORT}" if defined $url->{PORT};
  $hostkey=normHost($hostkey);

  my(@cookies)=();

  my($h,$hkey);

  for $hkey (keys %$db)
	{ 
	  if (
	}
  for (
  my(@keys)=(uc($scheme),lc($host),$label);

  local($_);

  while (@keys)
  { $_=shift(@keys);
    return undef if ! exists $h->{$_};
    $h=$h->{$_};
  }

  $h;
}

sub SaveCookie
{ my($this,$hostkey,$cname,$cvalue,$params)=@_;

  my($db)=$this->{DB};

  $hostkey=normHost($hostkey);

  $cname=uc($cname);

  $db->{$hostkey}={} if ! exists $db->{$hostkey};
  $db=$db->{$hostkey};

  $db->{$cname}={ NAME => $cname,
		  VALUE => $cvalue,
		  PARAMS => $params,
		};
}

# annotate some headers with an authority
sub HdrsAddAuth
{ my($this,$hdrs,$scheme,$auth)=@_;

  $hdrs->Add([AUTHORIZATION,
	"$scheme ".base64("$auth->{USERID}:$auth->{PASSWORD}")]);
}

1;
