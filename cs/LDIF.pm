#!/usr/bin/perl
#
# LDIF routines.
#	- Cameron Simpson <cs@zip.com.au> 28nov97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::LDIF;

sub new	{ my($class,$dn)=@_;
	  
	  bless { DN	=> $dn,
		}, $class;
	}

sub Put
	{ my($this,$sink)=@_;
	}

sub Get
	{ my($this,$src)=@_;

	  local($_);

	  while (defined ($_=$src->GetLine) && length)
		{
		  chomp;
		  s/
		}
	}

sub _GetAttr
	{

sub _PutAttr
	{ my($attr,$value,$sink,$base64)=@_;
	  $base64=0 if ! defined $base64;

	  $sink->Put($attr);

	  if ($base64)
		{ $sink->Put(':: ',

		{ ::need(cs::MIME::Base64);

		  my($value64)=cs::MIME::Base64::encode($value);
		  $value64 =~ s/\n/\n /g;

		  $sink->Put(':: ', $value64);
		}
	  else	{ $sink->Put(": $value");
		}

	  $sink->Put("\n");
	}

1;
