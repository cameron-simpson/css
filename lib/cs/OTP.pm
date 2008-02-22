#!/usr/bin/perl
#
# Access the opie authentication service and conduct an OTP transaction.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

use cs::Net::TCP;

package cs::OTP;

@cs::OTP::ISA=(cs::Net::TCP);

$cs::OTP::_Host='elph';	# cut to suit your site
$cs::OTP::_Port=9000;

sub new
	{ my($class,$host,$port)=@_;

	  $port=$cs::OTP::_Port if ! defined $port;
	  $host=$cs::OTP::_Host if ! defined $host;

	  ## warn "host=$host, port=$port";
	  my($tcp)=new cs::Net::TCP ($host,$port);

	  return undef if ! defined $tcp;

	  bless $tcp, $class;
	}
sub DESTROY
	{ my($this)=shift;
	  $this->PutFlush("QUIT\n");
	  $this->GetLine();
	}

# get a challenge for a key
sub Get
	{ my($this,$key)=@_;
	  $key =~ s/\s+//g;

	  ## warn "->OTP: GET $key\n";
	  $this->PutFlush("GET $key\n");

	  local($_)=$this->GetLine();
	  chomp;
	  ## warn "<-OTP: [$_]\n";
	  return undef unless /^2\d+\s+/;

	  $_=$';

	  my(@words)=grep(length,split(/\s+/));

	  wantarray ? @words : "@words";
	}

# try a response to a challenge
sub Try
	{ my($this,$key,$response)=@_;
	  $key =~ s/\s+//g;

	  $response =~ s/\s+$//;
	  $response =~ s/\s+/ /g;

	  ## warn "->OTP: TRY $key $response\n";
	  $this->PutFlush("TRY $key $response\n");

	  local($_)=$this->GetLine();
	  chomp;
	  ## warn "<-OTP: [$_]\n";

	  /^2/;
	}

1;
