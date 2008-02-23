#!/usr/bin/perl
#
# Package for network stuff.
#
#	$net'sockaddr		Pack/Unpack format for a sockaddr.
#	Net::hostname		Local host name.
#	Net::hostaddrs(hostname) Host addresses.
#	Net::hostnames(addr)	Host name and aliases.
#	Net::getaddr(SOCK) -> (family,port,addr)
#				Return address of socket.
#	Net::mkaddr(family,port,addr) -> sockaddr
#				Produce machine socket address.
#	Net::mkaddr_in(port,addr) -> sockaddr_in
#				Produce machine socket internet address.
#	Net::addr2a(addr) -> "x.x.x.x" Produce decimal rep of address.
#	Net::a2addr("x.x.x.x") -> addr Produce packed address from decimal rep.
#

use Socket;

package net;

$sockaddr='S n a4 x8';

sub hostname
	{ if (! defined $hostname)
		{ chop($hostname=`hostname`);
		  undef $hostname if ! length $hostname;
		}

	  return undef if ! defined $hostname;

	  $hostname;
	}

sub hostaddrs	# (hostname) -> @addrs
	{ local($name,$aliases,$type,$len,@hostaddrs)=gethostbyname($_[$[]);

	  @hostaddrs;
	}

sub hostnames	# (addr[,family]) -> (name,@aliases)
	{ local($addr,$fam)=@_;
	  $fam=Socket->AF_INET if ! defined $fam;
	  local($n,$a,$at,$l,@a)=gethostbyaddr($addr,$fam);

	  return undef if ! defined $n;

	  return ($n,@a) if wantarray;

	  $n;
	}

sub service	# (servname,protocolname) -> (port-number,proto-number)
	{ local($serv,$proto)=@_;
	  local($protoname,$etc1,$etc2);

	  if ($proto !~ /^\d+$/)
		{ ($protoname,$etc2,$proto)=getprotobyname($proto);
		}
	  else
	  { ($protoname,$etc2,$proto)=getprotobynumber($proto);
	  }

	  ($etc1,$etc2,$serv)=getservbyname($serv,$protoname)
		unless $serv =~ /^\d+$/;

	  ($serv,$proto);
	}

sub getaddr	# (SOCK) -> (family,port,myaddr)
	{ unpack($sockaddr,getsockname($_[$[]));
	}

sub mkaddr_in # (port,address) -> sockaddr_in
	{ &mkaddr(Socket->AF_INET,@_);
	}

sub mkaddr	# (family,port,address) -> sockaddr
	{ pack($sockaddr,@_);
	}

sub addr2a	# address -> "x.x.x.x"
	{ sprintf("%d.%d.%d.%d",unpack("CCCC",shift));
	}

sub a2addr	# "x.x.x.x" -> address
	{ local($a)=@_;

	  return undef unless /^(\d+)\.(\d+).(\d+).(\d+)$/;

	  pack("CCCC",$1,$2,$3,$4);
	}

1;
