#!/usr/bin/perl
#
# Code to handle URLs.
#	- Cameron Simpson <cs@zip.com.au> 11jan96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Net::TCP;
use cs::Upd;
use cs::Source;
use cs::HTML;
use cs::HTTP;

package cs::URL;

sub get
{ my($url)=shift;
  my($U)=new cs::URL $url;
  return undef if ! defined $U;
  $U->Get();
}

sub new	# url -> struct
{ my($class)=shift;
  local($_)=shift;

  my $this = {};
  my($scheme,$host,$port,$path,$query,$anchor);

  if (m|^(\w+):|)
  { $scheme=$1; $_=$'; }

  $port='';
  if (m|^//([^/:#?]+)(:(\d+))?|)
  { $host=$1;
    if (length($2))
    { $port=$3+0;
    }

    $_=$';
  }

  /^[^#?]*/;
  $path=$&;
  $_=$';

  if (/^\?([^#]*)/)
  { $query=$1; $_=$'; }

  if (/^\#(.*)/)
  { $anchor=$1; $_=$'; }

  $host=uc($host);

  $scheme=uc($scheme);

  # disambiguate FILE and FTP
  # a million curses on the idiot who decided to overload these!
  if ($scheme eq FILE)
  { if (length $host && $host ne LOCALHOST)
    { $scheme=FTP;
    }
  }
  elsif ($scheme eq FTP)
  { if (! length $host)
    { $scheme=FILE;
    }
  }

  $this->{SCHEME}=$scheme if length $scheme;
  $this->{HOST}=lc($host) if length $host;
  $this->{PORT}=urlPort($scheme,$port);
  $this->{PATH}=cs::HTTP::unhexify($path);
  $this->{QUERY}=$query;
  $this->{ANCHOR}=$anchor;

  bless $this, $class;
}

sub Text
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $url;

  ## warn "computing TEXT for ".cs::Hier::h2a($this,1);
  $url=lc($this->{SCHEME}).":" if defined $this->{SCHEME};
  $url.='//'.$this->HostPart() if defined $this->{HOST};
  $url.=$this->LocalPart($noanchor);

  ## warn "text=$url\n";

  $url;
}

sub HostPart
{ my($this)=@_;

  return "" if ! defined $this->{HOST};

  my($hp);

  $hp='';
  $hp.="$this->{USER}\@" if defined $this->{USER};
  $hp.=lc($this->{HOST}) if defined $this->{HOST};
  $hp.=":".lc($this->{PORT}) if defined $this->{PORT}
			      && $this->{PORT}
			      ne urlPort($this->{SCHEME},$this->{PORT});

  ## warn "HostPart=$hp\n";

  $hp;
}

sub IsAbs
{ my($this)=@_;

  defined $this->{SCHEME}
&& length $this->{SCHEME}
# schemes needing paths
&& ( ! grep($_ eq $this->{SCHEME},FILE,FTP,HTTP,HTTPS,GOPHER)
 || ( defined $this->{PATH} && length $this->{PATH} )
  )
# schemes needing hosts
&& ( ! grep($_ eq $this->{SCHEME},FILE,FTP,HTTP,HTTPS,GOPHER,NEWS,SNEWS)
 || ( defined $this->{HOST} && length $this->{HOST} )
  )
}

sub Abs	# (base_url,target_url,noQueryHack) -> abs_url
{ my($base,$target)=@_;
  # make target into an object
  $target=new cs::URL $target if ! ref $target;

##  warn "base url = ".$base->Text()."\n"
##      ."targ url = ".$target->Text()."\n";

  my($abs)=bless {};
  for (keys %$target)
  { $abs->{$_}=$target->{$_};
  }

  # short circuit
  return $abs if $abs->IsAbs();

  ## warn "NOT ABS ".$abs->Text();

  # we need an absolute URL to resolve against
  if (! $base->IsAbs())
  {
    my($context)=$base->Context();
    ## warn "context=[".$context->Text()."]";

    if (! defined $context)
    {
#	  ## warn "$::cmd: Abs(\""
#	      .$base->Text()
#	      ."\",\""
#	      .$target->Text()
#	      ."\"): no context for resolving LHS";
      return $target;
    }

    if (! $context->IsAbs())
    {
#	  ## warn "$::cmd: non-absolute context (\""
#	      .$context->Text()
#	      ."\") for \""
#	      .$base->Text()
#	      ."\"";
      return $target;
    }

    ## warn "call ABS from context";

    $base=$context->Abs($base);

    ## warn "ABS from CONTEXT(".$context->Text().")="
    ##	.$base->Text();
  }

  my($dodgy,$used_dodge)=(0,0);

  if (! defined $abs->{SCHEME}
   && defined $base->{SCHEME})
  { $abs->{SCHEME}=$base->{SCHEME};
  }
  elsif ($abs->{SCHEME} ne $base->{SCHEME})
  {
    $base=$target->Context($abs->{SCHEME});

    ## my(@c)=caller;
    ## warn "no context for ".cs::Hier::h2a($target,1)." from [@c]"
    ##	if ! defined $base;

    return $abs if ! defined $base;
    $dodgy=! $base->IsAbs();
  }

  if (! defined $abs->{HOST}
   && defined $base->{HOST})
  { $used_dodge=1;

    $abs->{HOST}=$base->{HOST};
    ## warn "set HOST to $base->{HOST}\n";

    if (defined $base->{PORT})
    { $abs->{PORT}=$base->{PORT};
    }
    else
    { delete $abs->{PORT};
    }

    # XXX - password code?
    if (defined $base->{USER})
    { $abs->{USER}=$base->{USER};
    }
    else
    { delete $abs->{USER};
    }
  }

  if ($abs->{PATH} !~ m:^/:)
  { $used_dodge=1;

    my($dirpart)=$base->{PATH};
    $dirpart =~ s:[^/]*$::;
    $dirpart="/" if ! length $dirpart;

    $abs->{PATH}="$dirpart$abs->{PATH}";
##    warn "interim path = $abs->{PATH}\n";
  }

  # trim /.
  while ($abs->{PATH} =~ s:/+\./:/:)
  {}

  # trim leading /..
  while ($abs->{PATH} =~ s:^/+\.\./:/:)
  {}

  # trim /foo/..
  while ($abs->{PATH} =~ s:/+([^/]+)/+\.\./:/:)
  {}

  if ($dodgy && $used_dodge)
  {
    warn "$::cmd: no default for scheme \"$abs->{SCHEME}\",\n";
    warn "\tusing \"".$base->Text()."\" instead, despite scheme mismatch\n";
  }

##  warn "RETURNING ABS = ".cs::Hier::h2a($abs,1);

  $abs;
}

sub LocalPart
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $l = $this->{PATH};

  if (length $this->{QUERY})
  { $l.="?$this->{QUERY}"; }

  if (! $noanchor && length $this->{ANCHOR})
  { $l.="#$this->{ANCHOR}"; }

  $l;
}

sub urlPort
	{ my($scheme,$port)=@_;
	  $scheme=uc($scheme);

	  (length $port
	      ? $port
	      : length $scheme
		  ? grep($_ eq $scheme,HTTP,FTP,GOPHER,HTTPS,NEWS,SNEWS)
			  ? cs::Net::portNum($scheme)
			  : ''
		  : '');
	}

sub Context
	{ my($this,$scheme)=@_;
	  $scheme=$this->{SCHEME} if ! defined $scheme
				  && defined $this->{SCHEME}
				  && length $this->{SCHEME};

	  ## warn "this=".cs::Hier::h2a($this,0).", scheme=[$scheme]";

	  my($context);

	  if (! defined $scheme)
		{ if (defined $ENV{HTTP_REFERER}
		   && length $ENV{HTTP_REFERER})
			{ $context=new cs::URL $ENV{HTTP_REFERER};
			}
		}
	  elsif ($scheme eq FILE)
		{ $context=fileContext();
		}

	  return undef if ! defined $context;
	  $context=new cs::URL $context if ! ref $context;
	  $context;
	}

sub fileContext
	{ my($dir)=@_;
	  ## warn "fileContext(@_): dir=[$dir]";

	  if (! defined $dir)
		{ ::need(Cwd);
		  $dir=cwd();
		  if (! defined $dir || ! length $dir)
			{ warn "$::cmd: cwd fails, using \"/\"";
			  $dir='/';
			}
		  else	{ ## warn "cwd=[$dir]";
			}
		}

  	  "file://localhost$dir";
	}

sub MatchesCookie($$;$)
{ my($this,$C,$now)=@_;

  ## my(@c)=caller;
  ## warn "this=$this, C=$C [@$C] from [@c]";

  substr(lc($this->{HOST}),-length($C->{DOMAIN}))
  eq $C->{DOMAIN}
  &&
  substr($this->{PATH},0,length($C->{PATH}))
  eq $C->{PATH}
  &&
  (! defined $now || $now <= $C->{EXPIRES});
}

1;
