#!/usr/bin/perl
#
# Core facilities for browsing web pages.
#	- Cameron Simpson <cs@zip.com.au> 20oct99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::URL;
use cs::HTTP;

package cs::Web::Browse;

$cs::Web::Browse::MaxConcurrent=4;
@cs::Web::Browse::URLqueue=();
%cs::Web::Browse::URLactive=();
$cs::Web::Browse::URLhandicap=0;

# queue a URL to fetch, optional attrs, callback and possible extra args
sub request
{ my $url = shift;
  die "request: no args!" if ! defined $url;
  my $attrs = shift;
  my $notify;
  if (::reftype($attrs) eq HASH)
  { $notify=shift;
  }
  else
  { $notify=$attrs;
    $attrs={};
  }


  ::log("request $url");

  my $U = new cs::URL $url;

  my $Q = { URL => $U,
	    ATTRS => $attrs,
	    NOTIFY => $notify,
	    ARGS => [ @_ ],
	  };

  if ($U->Scheme() eq FILE)
  { $Q->{HANDICAP}=1;
    _dispatch($Q);
  }
  else
  { _queue($Q);
    _tryIssue();
  }

  1;
}

sub _queue
{ my($Q)=@_;

  my $U = $Q->{URL};
  my $url = $
  push(@cs::Web::Browse::URLqueue,$Q);

# dispatch pending URLs while we're not too busy
sub _tryIssue
{ while (@cs::Web::Browse::URLqueue
      && keys %cs::Web::Browse::URLactive
       < $cs::Web::Browse::MaxConcurrent+$cs::Web::Browse::URLhandicap)
  { my $Q = shift(@cs::Web::Browse::URLqueue);
    _dispatch($Q);
  }
}

sub _dispatch
{ my($Q)=@_;

  my $handicap = (exists $Q->{HANDICAP} ? $Q->{HANDICAP} : 0);
  
  $cs::Web::Browse::URLhandicap+=$handicap;	# adjust "free" count
}

1;

  if ($url =~ /^file:/i)
  # direct filesystem fetch
  {
    $url=$';
    $hdrs=new cs::RFC822;

    warn "open file \"$url\"\n" if $::Verbose;
    if (! defined($data=cs::Source::open($url)))
	  { warn "$::cmd: can't open($url): $!\n";
	    $Xit=1;
	    return 0;
	  }
  }
  else
  # do an HTTP fetch
  {

    $rqhdrs=rqhdr($url);

    FETCH:
      while (! $ok)
      { if (! defined $url)
	{ warn "\$url undefined!";
	  $Xit=1;
	  return 0;
	}
	elsif (! length $url)
	{ warn "\$url empty!";
	  $Xit=1;
	  return 0;
	}
	elsif ($url eq '-')
	{ warn "read from STDIN\n" if $::Verbose;
	  $data=new cs::Source (FILE,STDIN);
	  $hdrs=new cs::RFC822 $data;
	  $ok=1;
	}
	else
	{ my($phttp)=httpconn($url);
	  if (! defined $phttp)
	  { warn "can't connect to proxy server: $!";
	    $Xit=1;
	    return 0;
	  }

	  my($rversion,$rcode,$rtext);

	  ## $::Debug && warn "hdrs=".cs::Hier::h2a($rqhdrs,1);
	  warn "GET $url\n" if $::Verbose;
	  ($rversion,$rcode,$rtext,$hdrs)=$phttp->Request(GET,$url,$rqhdrs);

	  if (! defined $rversion)
	  { warn "$::cmd: nothing from proxy for $url";
	    $Xit=1;
	    return 0;
	  }

	  warn "rcode=[$rcode], rtext=$rtext\n" if $::Verbose;
	  if ($rcode eq 302 || $rcode eq 301)
	  { my($newurl)=new cs::URL $url;

	    $ENV{HTTP_REFERER}=$url;
	    $url=$hdrs->Hdr(LOCATION);
	    chomp($url);
	    $url =~ s/\s+//g;

	    $newurl=$newurl->Abs($url);
	    $url=$newurl->Text();
	    $::Debug && warn "retrying with $url\n";
	    warn "REDIRECT to $url\n" if $::Verbose;
	  }
	  elsif ($rcode eq 401 && ! $triedauth)
	  {
	    my($uri)=new cs::URL $url;
	    my($host,$scheme,$label,$resp);

	    # get host
	    $host=$uri->{HOST};

	    # get challenge info from hdrs
	    ($scheme,$label)=$::auth->ParseWWW_AUTHENTICATE($hdrs);
	    die if ! defined $scheme;

	    # get response info
	    $resp=$::auth->GetAuth($scheme,$host,$label);
	    if (! ref $resp)
	    { warn "no login/password for $scheme/$host/$label";
	      $Xit=1;
	      return 0;
	    }

	    if ($::Verbose || $::Debug)
	    { warn "trying auth $resp->{USERID}:$resp->{PASSWORD}\n";
	    }
	    $rqhdrs=rqhdr($url);
	    $::auth->HdrsAddAuth($rqhdrs,$scheme,$resp);

	    ## $::Debug && warn "retry with ".cs::Hier::h2a($rqhdrs,1);
	    $triedauth=1;
	  }
	  elsif ($rcode ne 200)
	      { warn "$::cmd: $url\n\tunexpected response: $rversion $rcode $rtext\n";
		## $::Debug && warn "hdrs=".cs::Hier::h2a($hdrs,1,0,0,5)."\n";
		undef $outfile;
		$Xit=1;
		$data=$phttp->{IN};
		$ok=1;
	      }
	  else
	  { $data=$phttp->{IN};
	    $ok=1;
	  }
	}
      }
  }

  my($cte)=$hdrs->Hdr(CONTENT_TRANSFER_ENCODING);

  if ($::decodeContent && defined $cte && length $cte)
      { $data=cs::MIME::decodedSource($data,$cte);
      }

  if (! $ok)
	{
	  if (! open(CHILD,">&STDERR"))
		{ warn "$::cmd: can't dup STDERR: $!\n";
		  $Xit=1;
		  return 0;
		}
	}
  elsif (! @::ExecList)
	{ 

	  if (defined ($outfile))
		{ if (! open(CHILD,"> $outfile\0"))
			{ warn "$::cmd: can't write to $outfile: $!\n\tURL $url not saved\n";
			  $Xit=1;
			  return 0;
			}
		}
	  elsif (! open(CHILD,">&STDOUT"))
		{ warn "$::cmd: can't dup STDOUT: $!\n";
		  $Xit=1;
		  return 0;
		}
	}
  else
  { my($pid);

    if (! defined ($pid=open(CHILD,"|-")))
	{ die "$::cmd: can't pipe/fork to [@::ExecList] for $url: $!\n";
	}

    if ($pid == 0)
	# child - rig env and exec
	{ for ($hdrs->HdrNames())
		{ $ENV{'HTTP_'.cs::RFC822::hdrkey($_)}=$hdrs->Hdr($_);
		}

	  exec(@::ExecList) || die "$::cmd: exec(@::ExecList): $!";
	}
  }

  if ($::printHeaders)
	{ for ($hdrs->Hdrs())
		{ print CHILD $_, "\n";
		}

	  print CHILD "\n";
	}

  while (defined ($_=$data->Read()) && length)
	{ print CHILD $_;
	}

  if (! close(CHILD))
	{ warn "$::cmd: problem closing pipe to [@::ExecList] for $url: $!\n";
	  $Xit=1;
	  return 0;
	}

  return 1;
}
