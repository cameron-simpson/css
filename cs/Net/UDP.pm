#!/usr/bin/perl
#
# Package for UDP connections:
#
# UDP::portNum(port)
#	Return numeric value of port, given number or name.
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Socket;
use cs::Net;

package cs::Net::UDP;

{ local($name,$aliases);

  ($name,$aliases,$proto)=getprotobyname('udp');
  die "$0: can't look up udp protocol" unless defined($proto);
}

sub portNum { cs::Net::portNum('udp',@_); }

$cs::Net::UDP::SOCK='UDPSOCK0000';
sub _bind	# (port) -> FILE
	{ local($port)=@_;
	  local($name,$aliases);
	  local($FILE,$dummy);


	  ($port,$dummy)=cs::Net::service($port,'UDP::)
		unless $port =~ /^\d+$/;
	  $FILE=$SOCK++;
	  ((warn "socket: $!"), return undef)
		unless socket($FILE, Socket->PF_INET, Socket->SOCK_DGRAM, $UDP::proto);

	  $name=cs::Net::mkaddr_in($port, "\0\0\0\0");
	  ((warn "bind: $!"), return undef)
		unless bind($FILE, $name);

	  "UDP::".$FILE;
	}
sub new	{ my($class,$port)=@_;
	  my($sock);

	  return undef if ! defined($sock=_bind($port));

	  bless { Socket => $sock };
	}
sub DESTROY
	{ my($this)=@_;
	  close($this->{Socket});
	}

sub Send	# (this,data,port,addr) -> chars sent or undef
	{ my($this,$_,$port,$addr)=@_;
	  my($SOCK)=$this->{Socket};
	  local($_);

	  $_=send($SOCK,$_,0,cs::Net::mkaddr_in($port,$addr));

	  defined($_) ? $_ : undef;
	}

sub Recv	# %this -> ($data,$port,$addr) or undef
	{ my($this)=@_;
	  my($SOCK)=$this->{Socket};
	  my($from);
	  local($_);

	  $from=recv($SOCK,$_,65536,0);

	  return undef if ! defined $from;

	  return $_ unless wantarray;

	  my($family,$port,$addr)=unpack($net'sockaddr,$from);

	  ($_,$port,$addr);
	}

1;	# for require
