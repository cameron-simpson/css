#!/usr/bin/perl
#
# Do HTTP-related stuff.
#

use Net::TCP;
use HTML;
require 'flush.pl';

package http;

$PORT=80;	# default port number for HTTP

sub rwopen	# (host[,port]) -> FILEHANDLE
	{ my($host,$port)=@_;
	  $port=$PORT if !defined($port) || !length($port);
	  Net::TCP::RWOpen($host,$port);
	}

sub request	# (file,host[,port)
	{ my($file)=shift;
	  my($FILE);

	  return undef unless defined($FILE=&rwopen);

	  &'printflush($FILE,"GET $file\n");
	  $FILE;
	}

sub get		# (file,host[,port]) -> @lines
	{ my($FILE);
	  return undef unless defined($FILE=&request);
	  my(@lines)=<$FILE>;
	  close($FILE);

	  wantarray ? @lines : join('',@lines);
	}

sub parserequest	# request -> method,url,trailingjunk
	{ local($_)=shift;
	  my($method,$url,$tail);

	  return undef unless /^\s*([A-Za-z]+)\s+(\S+)\s*(.*)/;

	  $method=$1; $url=$2; $tail=$3;
	  $method =~ tr/a-z/A-Z/;

	  ($method,&HTML::unquote($url),$tail);
	}

1;
