#!/usr/bin/perl
#
# Do things with netscape stuff.
#	- Cameron Simpson <cs@zip.com.au> 10aug99
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Netscape;

@cs::Netscape::ISA=qw();

# read the cookies file and return a list of cookie specs
sub cookies
{ my($file)=@_;
  $file="$ENV{HOME}/private/netscape/cookies" if ! defined $file;

  return () if ! open(COOKIES,"< $file\0");

  my @cookies=();

  COOKIE:
  while (<COOKIES>)
  { next COOKIE unless /^([^#\s]\S*)\s+(TRUE|FALSE)\s+(\/\S*)\s+(TRUE|FALSE)\s+(\d+)\s+(\S+)\s+(\S+)$/;

    push(@cookies, { DOMAIN => lc($1),
		     DOMAINWIDE => $2 eq TRUE,
		     PATH => $3,
		     SECURE => $4 eq TRUE,
		     EXPIRES => $5+0,
		     NAME => $6,
		     VALUE => $7,
		   });
  }

  @cookies;
}

1;
