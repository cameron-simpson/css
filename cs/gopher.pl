#!/usr/bin/perl
#
# Do Gopher-related stuff.
#

require 'cs/tcp.pl';
require 'flush.pl';

package gopher;

$PORT=70;	# default port number for Gopher

sub rwopen	# (host[,port]) -> FILEHANDLE
	{ local($host,$port)=@_;
	  $port=$PORT if !defined($port);
	  &tcp'rwopen($host,$port);
	}

sub request	# (req,host[,port)
	{ local($req)=shift;
	  local($FILE);

	  return undef unless defined($FILE=&rwopen);

	  &'printflush($FILE,"$req\r\n");
	  $FILE;
	}

sub get		# (req,host[,port]) -> @lines
	{ local($FILE);
	  return undef unless defined($FILE=&request);
	  local(@lines)=<$FILE>;
	  close($FILE);

	  local($_);

	  for (@lines)	{ s/\r?\n$//; }

	  if (defined($_=pop @lines) && $_ ne '.')
		{ push(@lines,$_);
		}

	  wantarray ? @lines : join('',@lines);
	}

sub fields
	{ split(/\t/,shift);
	}

1;
