#!/usr/bin/perl
#
# Package for network stuff.
#
#	$cs::Net::sockaddr		Pack/Unpack format for a sockaddr.
#	&cs::Net::hostname		Local host name.
#	&cs::Net::hostaddrs(hostname) Host addresses.
#	&cs::Net::hostnames(addr)	Host name and aliases.
#	&cs::Net::getaddr(SOCK) -> (family,port,addr)
#				Return address of socket.
#	&cs::Net::mkaddr(family,port,addr) -> sockaddr
#				Produce machine socket address.
#	&cs::Net::mkaddr_in(port,addr) -> sockaddr_in
#				Produce machine socket internet address.
#	&cs::Net::addr2a(addr) -> "x.x.x.x" Produce decimal rep of address.
#	&cs::Net::a2addr("x.x.x.x") -> addr Produce packed address from decimal rep.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Socket;
use cs::Misc;

package cs::Net;

$cs::Net::_sockaddr='S n a4 x8';
$cs::Net::_ports{TCP}={
	  FTP		=> 21,
	  TELNET	=> 23,
	  SMTP		=> 25,
	  GOPHER	=> 70,
	  HTTP		=> 80,
	  HTTPS		=> 443,
	};

sub service($$)
{ my($protocol,$port)=@_;
  $protocol=uc($protocol);

  if ($protocol eq TCP)	{ ::need(cs::Net::TCP);
			  return new cs::Net::TCP($port);
			}

  warn "service($protocol,$port): unsupported protocol";

  return undef;
}

sub conn	# (host,portspec[,protocol]) -> cs::Port
{ my($protocol,$host,$port)=@_;
  $protocol=uc($protocol);

  if ($protocol eq TCP)	{ ::need(cs::Net::TCP);
			  return new cs::Net::TCP ($host,$port);
			}

  warn "conn($protocol,$host,$port): unsupported protocol";

  return undef;
}

sub portNum($;$)
{ my($portspec,$proto)=@_;
  if (! defined $proto)	{ $proto=TCP; }
  else			{ $proto=uc($proto); }

  return $portspec+0 if $portspec =~ /^\d+$/;

  $portspec=uc($portspec);
  if (exists $cs::Net::_ports{$proto}
   && exists $cs::Net::_ports{$proto}->{$portspec})
  { return $cs::Net::_ports{$proto}->{$portspec};
  }

  my($servnam,$aliases,$port,$protonum)=getservbyname(lc($portspec),
						      lc($proto));

  return $port if defined $port;

  warn "no service \"$portspec\" for protocol \"$proto\": $!";
  my(@c)=caller;warn "called from [@c]";

  return undef;
}

sub a2addr
{ local($_)=@_;
  my(@a);

  if (/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/)
  { @a=pack("CCCC",$1,$2,$3,$4);
  }
  else
  { @a=hostaddrs($_);
  }

  wantarray ? @a : shift(@a);
}

sub hostaddrs	# (hostname) -> @addrs
{ my($name,$aliases,$type,$len,@hostaddrs)=gethostbyname(shift);
  @hostaddrs;
}

###########################
sub hostname
{ if (! defined $cs::Net::Hostname)
  { chop($cs::Net::Hostname=`hostname`);
    undef $cs::Net::Hostname if ! length $cs::Net::Hostname;
  }

  return undef if ! defined $cs::Net::Hostname;

  $cs::Net::Hostname;
}

sub hostnames	# (addr[,family]) -> (name,@aliases)
{ my($addr,$fam)=@_;
  $fam=Socket->AF_INET if ! defined $fam;
  my($n,$a,$at,$l,@a)=gethostbyaddr($addr,$fam);

  return undef if ! defined $n;

  return ($n,@a) if wantarray;

  $n;
}

sub getaddr	# (SOCK) -> (family,port,myaddr)
{ unpack($cs::Net::_sockaddr,getsockname(shift));
}

sub mkaddr_in # (port,address) -> sockaddr_in
{ mkaddr(Socket->AF_INET,@_);
}

sub mkaddr	# (family,port,address) -> sockaddr
{ pack($cs::Net::_sockaddr,@_);
}

sub addr2a	# address -> "x.x.x.x"
{ sprintf("%d.%d.%d.%d",unpack("CCCC",shift));
}

1;
