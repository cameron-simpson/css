#!/usr/bin/perl
#
# Package for UDP connections:
#
#	$udp'proto		Protocol number for UDP.
#	&udp'bind(port) -> FILE	Make a UDP socket at port (0 for any)
#

use Socket;
use Net;

package udp;

{ local($name,$aliases);

  ($name,$aliases,$proto)=getprotobyname('udp');
  die "$0: can't look up udp protocol" unless defined($proto);
}

$SOCK='UDPSOCK0000';
sub udp'bind	# (port) -> FILE
	{ local($port)=@_;
	  local($name,$aliases);
	  local($FILE,$dummy);


	  ($port,$dummy)=Net::service($port,'udp')
		unless $port =~ /^\d+$/;
	  $FILE=$SOCK++;
	  ((warn "socket: $!"), return undef)
		unless socket($FILE, Socket->PF_INET, Socket->SOCK_DGRAM, $udp'proto);

	  $name=Net::mkaddr_in($port, "\0\0\0\0");
	  ((warn "bind: $!"), return undef)
		unless bind($FILE, $name);

	  "udp'".$FILE;
	}

sub udp'send	# (sock,data,port,addr) -> chars sent or undef
	{ local($SOCK,$_,$port,$addr)=@_;

	  $_=send($SOCK,$_,0,Net::mkaddr_in($port,$addr));

	  defined($_) ? $_ : undef;
	}

sub udp'recv	# ($SOCK) -> ($data,$port,$addr) or undef
	{ local($_,$from);

	  $from=recv(shift,$_,65536,0);

	  return undef if ! defined $from;

	  local($family,$port,$addr)=unpack($net'sockaddr,$from);

	  wantarray ? ($_,$port,$addr) : $_;
	}

1;	# for require
