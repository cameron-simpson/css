#!/usr/bin/perl
#
# Parser for PGP data structures.
# Based on RFC 1991 - PGP Message Exchange Formats.
#	- Cameron Simpson <cs@zip.com.au> 26may98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use Math::BigInt;

package cs::PGP;

@cs::PGP::ISA=qw(cs::Tokenise);

# new parser for binary PGP packet stream
sub new
	{ my($class,$s)=@_;

	  my($this)=(new cs::Tokenise $s, \&_match);

	  bless $this, $class;
	}

sub _match	# (Data,State) -> (token,tail) or undef
	{ local($_)=shift;
	  my($State)=shift;
	  my($tok,$tail);

	  return undef if ! length;

	  my($cipherType,$packetType,$packetLenLen,$tail)
		=_getCipherTypeByte($_);

	  if (length $tail < $packetLenLen)
		{ return undef;
		}

	  # get packet length
	  my($plen)=_wnf2BigInt(substr($tail,$[,$packetLenLen));
	  substr($tail,$[,$packetLenLen)='';

	  ($tok,$tail);
	}

sub _getStringField	# data -> (string,remains)
	{ local($_)=shift;
	
	  if (! length)	{ warn "$::cmd: no length field for StringField";
			  return ();
			}

	  my($len)=unpack("C",$_);
	  substr($_,$[,1)='';

	  if ($len > length)
		{ warn "$::cmd: insufficient data for length $len [$_]";
		  return ();
		}

	  my($data)=substr($_,$[,$len);

	  return ($data,substr($_,$[+$len));
	}

sub _wnf2BigInt	# bytestring => BigInt
	{ local($_)=shift;

	  my($B32)=(new Math::BigInt 65536)*65536;
	  my($I)=new Math::BigInt 0;

	  # fast
	  while (length >= 4)
		{ $I*=$B32+unpack("L",substr($_,-4));
		  substr($_,-4)='';
		}

	  # then slow
	  while (length)
		{ $I*=256+unpack("C",substr($_,-1));
		  substr($_,-1)='';
		}

	  $I;
	}

sub _getMultiprecisionField	# data => (BigInt,remains)
	{ local($_)=shift;

	  if (length < 2)	{ warn "$::cmd: insufficient data for MultiprecisionField [$_]";
				  return undef;
				}

	  my($bytes)=int((unpack("S",$_)+7)/8);
	  substr($_,$[,2)='';

	  if ($bytes > length)	{ warn "$::cmd: insufficient data for MultiprecisionField with WNF of length $bytes [$_]";
				  return undef;
				}

	  my($I)=_wnf2BigInt($substr($_,$[,$bytes));

	  return ($I,substr($_,$[+$bytes));
	}

1;
