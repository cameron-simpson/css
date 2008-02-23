#!/usr/bin/perl
#
# Code to support the FSP protocol.
#
#	$fsp'port	Current FSP port.
#	$fsp'addr	Current FSP address.
#

# require 'cs/net.pl';
require 'cs/udp.pl';

package fsp;

$CC_VERSION	= 0x10;	# return version string
$CC_ERR		= 0x40;	# error response from server
$CC_GET_DIR	= 0x41;	# get a directory listing
$CC_GET_FILE	= 0x42;	# get a file
$CC_UP_LOAD	= 0x43;	# open a file for writing
$CC_INSTALL	= 0x44;	# close a file opened for writing
$CC_DEL_FILE	= 0x45;	# delete a file
$CC_DEL_DIR	= 0x46;	# delete directory
$CC_GET_PRO	= 0x47;	# get directory protection
$CC_SET_PRO	= 0x48;	# set directory protection
$CC_MAKE_DIR	= 0x49;	# make a directory
$CC_BYE		= 0x4A;	# finsih a session
$CC_GRAB_FILE	= 0x4B;	# atomic get+delete a file
$CC_GRAB_DONE	= 0x4C;	# atomic get+delete a file done
$CC_LIMIT	= 0x80;	# > 0x7F for future control block extension

undef $SOCK;

# construct a packet for dispatch
sub fsp'mkpacket	# (cmd, cksum, key, seqnum, pos, msg)
	{ local($cmd,$cksum,$key,$seqnum,$pos,$_)=@_;

	  pack('c C S S S L',$cmd,$cksum,$key,$seqnum,length,$pos).$_;
	}

# decode a packet
sub fsp'decode		# (packet) -> (cmd, cksum, key, seqnum, $msglen, pos, msg)
	{ unpack('c C S S S L C*',shift);
	}

# dispatch packet to current server
sub fsp'send		# (packet)
	{ &fsp'init;
	  $ENV{'FSP_HOST'}=Net::addr2a($
# ensure we have a socket
sub fsp'init
	{ if (!defined($fsp'SOCK))
		{ if (!defined($fsp'SOCK=&udp'bind(0)))
			{ die "fsp'init: can't udb'bind(0): $!";
			}
		  
		  $fsp'host=Net::a2addr($ENV{'FSP_HOST'});
		  $fsp'port=$ENV{'FSP_PORT'}+0;
		}
	}

# return version string of current FSP server
sub fsp'version		# (void) -> version string or undef
	{ &fsp'init;
	}
1;
