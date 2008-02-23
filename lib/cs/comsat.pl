#!/usr/bin/perl
#
# Package for comsat.
#
#	$net'sockaddr		Pack/Unpack format for a sockaddr.
#	$net'hostname		Local host name.
#	$net'hostaddr		Local host address.
#	Net::getaddr(SOCK) -> (family,port,addr)
#				Return address of socket.
#	Net::mkaddr(family,port,addr) -> sockaddr
#				Produce machine socket address.
#	Net::mkaddr(port,addr) -> sockaddr_in
#				Produce machine socket internet address.
#	Net::addr2a(addr) -> "x.x.x.x" Produce decimal rep of address.
#

require 'cs/udp.pl';

package comsat;

{ local($dummy);

  ($satport,$dummy)=Net::service('comsat','udp');
  die "$0: can't look up comsat/udp" unless defined($satport);
}

sub send	# (sock,addr,message)
	{ local($SOCK,$addr,$message)=@_;

	  Net::send($SOCK,$message,$satport,$addr);
	}

1;
