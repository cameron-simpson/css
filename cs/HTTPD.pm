#!/usr/bin/perl
#
# HTTP daemon code.
#	- Cameron Simpson <cs@zip.com.au> 27oct95
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Net::TCP;
use cs::Source;
use cs::Sink;
use cs::RFC822;
use cs::URL;
use cs::HTTP;	# protocol definitions
use cs::CGI;

package cs::HTTPD;

sub new
	{
	  my($class,$port,$methods,$state)=@_;
	  $port=80 if ! defined $port;
	  $methods={} if ! defined $methods;

	  my($this)={};
	  my($serv)=new cs::Net::TCP $port;

	  if (! defined $serv)
		{ warn "can't attach service to port $port";
		  return undef;
		}

	  $this->{SERVICE}=$serv;
	  $this->{METHODS}=$methods;
	  $this->{STATE}=$state;

	  bless $this, $class;
	}

sub Serve
	{ my($this,$flags)=@_;
	  $flags=$cs::Net::TCP::F_ONCE if ! defined $flags;

	  my($serv)=$this->{SERVICE};

	  $serv->Serve($flags,\&_serve,$this);
	}

sub _serve
	{ my($conn,$this)=@_;

	  my($rq)=$conn->GetLine();
	  if (! defined $rq || ! length $rq)
		{ ## warn "no request!";
		  return undef;
		}

	  chomp($rq);

	  if ($rq !~ /^\s*(\S+)\s+(\S+)\s+http\/(\d+\.\d+)/i)
		{ warn "[$rq]: bad request";
		  $this->Respond($conn,$cs::HTTP::E_BAD,"Bad Request [$rq]");
		  return undef;
		}

	  my($method,$uri,$httpvers)=($1,$2,$3);
	  $method=uc($method);

	  my($H)=new cs::RFC822 $conn;
	  if (! defined $H)
		{ warn "[$rq]: no headers";
		  $this->Respond($conn,$cs::HTTP::E_BAD,"Bad Request [$rq]");
		  return undef;
		}

	  my($RQ)={ CONN	=> $conn,
		    URI		=> $uri,
		    HDRS	=> $H,
		    VERSION	=> $httpvers,
		    METHOD	=> $method,
		  };

	  my($cgipath);

	  if (($method eq GET || $method eq POST)
	   && exists $this->{METHODS}->{CGI}
	   && defined ($cgipath=isCGI($this->{METHODS}->{CGI},$uri)))
		{
		  # default response
		  $RQ->{RESPONSE}=[200,OK];

		  # make sink to collect data, and attach CGI object to it
		  $RQ->{CGISINK}=[];

		  my($cgi)=MkCGI($RQ,
				 $cgipath,
				 (new cs::Sink (ARRAY,$RQ->{CGISINK})));

		  # call handler for CGI
		  my($sub)=$this->{METHODS}->{CGI}->{$cgipath}->{HANDLER};
		  &$sub($this,$RQ,$cgi);

		  # generate response but no headers or body
		  $this->Respond($conn,@{$RQ->{RESPONSE}},NONE);

		  # output CGI script's data
		  $conn->Put(@{$RQ->{CGISINK}});

		  return undef;
		}

	  if (! defined $this->{METHODS}->{$method})
		{ warn "[$rq]: method \"$method\" not implemented";
		  $this->Respond($conn,$cs::HTTP::E_NOT_IMPL,"method $method not implemented");
		  return undef;
		}

	  &{$this->{METHODS}->{$method}}($this,$RQ);
	}

sub isCGI
	{ my($cgispec,$uri)=@_;
	  $uri=new cs::URL $uri if ! ref $uri;

	  local($_);

	  for (keys %$cgispec)
		{ if ($uri->{PATH} eq $_
		   || substr($uri->{PATH},$[,length($_)+1) eq "$_/")
			{ return $_;
			}
		}

	  return undef;
	}

sub Respond
	{ my($this,$conn,$code,$reason,$hdrs)=@_;

	  # warn "HTTPD::Respond(@_)";
	  if ($code !~ /^\d{3}$/)
		{ my(@c)=caller;
		  # warn "RESPOND: $cs::HTTP::_HTTP_VERSION $code $reason from [@c]";
		}

	  $conn->Put("$cs::HTTP::_HTTP_VERSION $code $reason\r\n");
	  if (defined $hdrs && $hdrs eq NONE)
		{}
	  elsif (defined $hdrs)
		{ $hdrs->WriteItem($conn);
		}
	  else
	  { $conn->Put("Content-Type: text/plain\r\n\r\n",
		       "Response code is: $cs::HTTP::_HTTP_VERSION $code $reason\r\n");
	  }
	}

# XXX - must decode before return
sub GetPOSTData
	{ my($this,$conn)=@_;
	  my($data)={};

	  local($_);

	  while (defined ($_=$conn->GetLine()) && length && /^([^=\s]+)=(\S+)/)
		{ $data->{$1}=$2;
		}

	  $data;
	}

# called from a method implementation to arrange a CGI subimplementation
sub MkCGI	# (rq,cgipath,[sink]) -> cs::CGI
	{ my($rq,$cgipath,$sink)=@_;
	  $sink=$rq->{CONN} if ! defined $sink; 

	  ## my(@c)=caller;
	  ## warn "from [@c]: rq=".cs::Hier::h2a($rq,0,0,1)." cgipath=[$cgipath]";

	  my($env)={};

	  my($i);

	  my($uri)=new cs::URL $rq->{URI};

	  # compose the environment
	  $env->{SERVER_SOFTWARE}='cs-HTTPD-perl/0.1';

	  # spec from http://hoohoo.ncsa.uiuc.edu/cgi/interface.html
	  $env->{GATEWAY_INTERFACE}='CGI/1.1';

	  $env->{SERVER_PROTOCOL}='HTTP/'.$rq->{VERSION};

	  $env->{REQUEST_METHOD}=$rq->{METHOD};

	  $env->{SCRIPT_NAME}=$cgipath;
	  if (substr($uri->{PATH},$[,length $cgipath) eq $cgipath)
		{ $env->{PATH_INFO}=substr($uri->{PATH},$[+length($cgipath));
		}
	  else	{ $env->{PATH_INFO}="";
		}

	  $env->{PATH_TRANSLATED}=$env->{PATH_INFO};
	  $env->{QUERY_STRING}=(exists $uri->{QUERY} ? $uri->{QUERY} : "");

	  my($H)=$rq->{HDRS};
	  $env->{CONTENT_TYPE}=$H->Hdr(CONTENT_TYPE);

	  for ($H->HdrNames())
		{ $env->{HTTP_.cs::RFC822::hdrkey($_)}=$H->Hdr($_);
		}

	  my($hosthdr)=scalar($H->Hdr(HOST));
	  if (defined $hosthdr)
		{ if ($hosthdr =~ /:(\d+)$/)
			{ $env->{SERVER_NAME}=$`;
			  $env->{SERVER_PORT}=$1;
			}
		  else	{ $env->{SERVER_NAME}=$hosthdr;
			  $env->{SERVER_PORT}=$cs::HTTP::Port;
			}
		}
	  else	{ $env->{SERVER_NAME}=(exists $uri->{HOST}
					? $uri->{HOST}
					: 'localhost');
		  $env->{SERVER_PORT}=(exists $uri->{PORT}
					? $uri->{PORT}
					: '');
		}

	  new cs::CGI ($rq->{CONN},$env,$sink);
	}

sub OTPChallenge
	{
	}

1;
