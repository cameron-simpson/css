#!/usr/bin/perl
#
# SOCKS5 protocol.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::SOCKS;

@cs::SOCKS::ISA=qw();

$cs::SOCKS::VERSION=5;

sub new
	{ my($class)=shift;
	  my($tcp)=new cs::Net::TCP @_;

	  $tcp->Put(chr($VERSION).chr(1).chr(0));

	  local($_);

	  $_=$tcp->NRead(2);
	  if (length != 2)
		{ warn "short reply [$_] from ".cs::Hier::h2a($tcp,0);
		  return undef;
		}

	  my($sver,$smethod)=map(ord($_),split(//));

	  if ($sver != $VERSION)
		{ warn "bad version ($sver)"
	      || ord(substr($_,$[+1,1)) != 0)
		{ warn "bad reply [$_] from ".cs::Hier::h2a($tcp,0);
		  return undef;
		}

	  
	}

sub loadconf
	{ my($cf)=@_;
	  $cf='/usr/local/etc/libsocks5.conf' if ! defined $cf;

	  my($s)=new cs::Source (PATH, $cf);
	  return undef if ! defined $s;

	}

1;
