#!/usr/bin/perl
#
# My proxy server, to share caches.
#	- Cameron Simpson <cs@zip.com.au> 10sep96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::HTTPD::Proxy;

@cs::HTTPD::Proxy::ISA=(HTTPD);

@cs::HTTPD::Proxy::Proxy=('proxy',8080);

# XXX - should be in URL.pm
sub useProxy
{ my($uri)=@_;
  @Proxy;
}

sub Get	# (fullget,URI,Headers) -> Source
{ my($this,$fullget,$uri,$h)=@_;
  my($u)=new cs::URL $uri;
  my($s);

  print STDERR "HTTPD::Proxy::Get(@_)\n";
  if (! length $u->{PROTO}
   || ! length $u->{HOST}
   || ($u->{PROTO} eq FILE && lc($u->{HOST}) eq 'localhost'))
	# reject incomplete or local requests
	{ print STDERR "local path - rejecting\n";
	  $this->Respond($HTTPD::E_NOT_IMPL,"I expected a proxy request [$uri]");
	  return;
	}

  # pre: HOST and PROTO are set in this URL
  my(@proxy)=useProxy($u);

  if (@proxy)
	{ my($http)=new cs::HTTP @proxy;

	  print STDERR "using proxy (@proxy)\n";
	  if (! defined $http)
		{ $this->Respond($HTTP::E_INTERNAL,"can't connect to proxy [@proxy]");
		  return;
		}

	  if (! $http->Get($uri,$h))
		{ if ($http->{RCODE} !~ /^\d\d\d$/)
			{ $this->Respond($HTTP::E_INTERNAL,"bad response from proxy [@proxy]");
			  return;
			}

		  $this->Respond($http->{RCODE},"[@proxy]: $http->{RTEXT}");
		  return;
		}

	  my($s)=$this->Sink();
	  $this->Respond($http->{RCODE},
			 "[@proxy]: $http->{RTEXT}",
			 $http->{HDRS});
	  $http->Source()->CopyTo($s);
	  exit 0;
	}

  $s=$u->Get();

  if (! defined $s)
	{ $this->Respond($HTTPD::E_NOT_FOUND,"$!");
	  return;
	}

  $this->Respond($HTTPD::R_OK,'OK');

  $this->Put("Content-Type: text/plain\n\n");
  $this->Flush();

  system("ls -ld $u->{PATH}; cat $u->{PATH}");
}

1;
