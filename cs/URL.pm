#!/usr/bin/perl
#
# Code to handle URLs.
#	- Cameron Simpson <cs@zip.com.au> 11jan96
#

=head1 NAME

cs::URL - manipulate URLs

=head1 SYNOPSIS

use cs::URL;

=head1 DESCRIPTION

This module implements methods for dealing with URLs.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Net::TCP;
use cs::Upd;
use cs::Source;
use cs::HTML;
use cs::HTTP;

package cs::URL;

=head1 GENERAL FUNCTIONS

=over 4

=item get(I<url>)

Create a B<cs::URL> object from the I<url> supplied
and call the B<Get> method below.

=cut

sub get($)
{ my($url)=shift;
  my($U)=new cs::URL $url;
  return undef if ! defined $U;
  $U->Get();
}

=item urlPort(I<scheme>,I<port>)

Given a I<scheme> and I<port>,
return the numeric value of I<port>.
If the I<port> parameter is omitted,
return the default port number for I<scheme>.

=cut

sub urlPort($$)
{ my($scheme,$port)=@_;
  $scheme=uc($scheme);

  (length $port
      ? cs::Net::portNum($port)
      : length $scheme
	  ? grep($_ eq $scheme,HTTP,FTP,GOPHER,HTTPS,NEWS,SNEWS)
		  ? cs::Net::portNum($scheme)
		  : ''
	  : '');
}

=back

=head1 OBJECT CREATION

=over 4

=item new(I<url>,I<base>)

Create a new B<cs::URL> object from the I<url> string supplied.
If I<base> (a B<cs::URL> object or URL string) is supplied

=cut

sub new($$;$)
{ my($class)=shift;
  local($_)=shift;
  my($base)=shift;

  # turn base URL into object
  if (defined $base && ! ref $base)
  { my $nbase = new cs::URL $base;
    if (! defined $nbase)
    { warn "$::cmd: new $class \"$_\", \"$base\": second URL invalid!";
      undef $base;
    }
    else
    { $base=$nbase;
    }
  }

  my $this = {};
  my($scheme,$host,$port,$path,$query,$anchor);
  my $ok = 1;

  if (m|^(\w+):|)
  { $scheme=$1;
    $_=$';
  }
  elsif (defined $base)
  { $scheme=$base->Scheme();
  }
  else
  { $ok=0;
  }

  $port='';
  if (m|^//([^/:#?]+)(:(\d+))?|)
  { $host=$1;

    if (length($2))
    { $port=$3+0;
    }

    $_=$';
  }
  elsif (defined $base)
  { $host=$base->Host();
    $port=$base->Port();
  }
  else
  { $ok=0;
  }

  return undef if ! $ok;

  if ($scheme eq HTTP || $scheme eq FILE || $scheme eq FTP)
  {
    if ($scheme eq HTTP)
    { /^[^#?]*/;
      $path=$&;
      $_=$';
    }
    else
    { $path=$_;
      $_='';
    }

    if (substr($path,$[,1) ne '/')
    # relative path, insert base's path
    { if (defined $base)
      { my $dirpart = $base->{PATH};
	$dirpart =~ s:[^/]*$::;
	$dirpart="/" if ! length $dirpart;

	$path="$dirpart$path";

	# trim /.
	while ($path =~ s:/+\./:/:)
	{}

	# trim leading /..
	while ($path =~ s:^/+\.\./:/:)
	{}

	# trim /foo/..
	while ($path =~ s:/+([^/]+)/+\.\./:/:)
	{}
      }
    }
  }
  else
  { $path=$_;
    $_='';
  }

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

  $this->{SCHEME}=$scheme;
  $this->{HOST}=lc($host);
  $this->{PORT}=urlPort($scheme,$port);
  $this->{PATH}=cs::HTTP::unhexify($path);
  $this->{QUERY}=$query;
  $this->{ANCHOR}=$anchor;

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Abs(I<relurl>)

DEPRECIATED. Use T<new cs::URL I<relurl>, $this> instead.
Return a new B<cs::URL> object
from the URL string I<relurl> with the current URL as base.

=cut

sub Abs($$)
{ my($base,$target)=@_;
  new cs::URL $target, $base;
}

sub _OldAbs($$)
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

=item IsAbs()

DEPRECIATED.
Test whether this URL is an absolute URL.
This is legacy support for relative URLs
which I'm in the process of removing
in favour of a method to return the relative difference
between two URLs as a text string
and to generate a new URL object given a base URL and a relative URL string.

=cut

sub IsAbs($)
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

=item Context

DEPRECIATED.
Return a URL representing the current context
for the specified I<scheme>.
Use this URL's I<scheme> if the I<scheme> parameter is omitted.
This is a very vague notion,
drawing on the B<HTTP_REFERER> environment variable
as a last resort.

=cut

sub Context($;$)
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
  { $context=_fileContext();
  }

  return undef if ! defined $context;
  $context=new cs::URL $context if ! ref $context;
  $context;
}

sub _fileContext
{ my($dir)=@_;
  ## warn "fileContext(@_): dir=[$dir]";

  if (! defined $dir)
  { ::need(Cwd);
    $dir=cwd();
    if (! defined $dir || ! length $dir)
    { warn "$::cmd: cwd fails, using \"/\"";
      $dir='/';
    }
    else
    { ## warn "cwd=[$dir]";
    }
  }

  "file://localhost$dir";
}

=item Text(I<noanchor>)

Return the textual representation of this URL.
Omit the B<#I<anchor>> part, if any, if the I<noanchor> parameter is true
(it defaults to false).

=cut

sub Text($;$)
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $url;

  ## warn "computing TEXT for ".cs::Hier::h2a($this,1);
  my $SC=$this->{SCHEME};
  $url=lc($SC).":" if length $SC;
  if ($SC eq FILE || $SC eq HTTP || $SC eq HTTPS || $SC eq FTP)
  { $url.='//'.$this->HostPart() if defined $this->{HOST};
  }
  $url.=$this->LocalPart($noanchor);

  ## warn "text=$url\n";

  $url;
}

=item Scheme()

Return the scheme name for this URL.

=cut

sub Scheme($)
{ shift->{SCHEME};
}

=item Host()

Return the host name for this URL.

=cut

sub Host($)
{ shift->{HOST};
}

=item Port()

Return the port number for this URL.

=cut

sub Port($)
{ shift->{PORT};
}

=item HostPart()

Return the B<I<user>@I<host>:I<port>> part of the URL.

=cut

sub HostPart($)
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

=item LocalPart(I<noanchor>)

Return the local part (B</path#anchor>) of this URL.
Omit the B<#I<anchor>> part, if any, if the I<noanchor> parameter is true
(it defaults to false).

=cut

sub LocalPart($;$)
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $l = $this->{PATH};

  if (length $this->{QUERY})
  { $l.="?$this->{QUERY}"; }

  if (! $noanchor && length $this->{ANCHOR})
  { $l.="#$this->{ANCHOR}"; }

  $l;
}

=item MatchesCookie(I<cookie>,I<when>)

Given a I<cookie>
as a hasref with B<HOST>, B<DOMAIN>, B<PATH> and B<EXPIRES> fields
and a time I<when> (which defaults to now),
return whether the cookie should be associated with this URL.

=cut

sub MatchesCookie($$;$)
{ my($this,$C,$when)=@_;
  $when=time if ! defined $when;

  ## my(@c)=caller;
  ## warn "this=$this, C=$C [@$C] from [@c]";

  substr(lc($this->{HOST}),-length($C->{DOMAIN}))
  eq $C->{DOMAIN}
  &&
  substr($this->{PATH},0,length($C->{PATH}))
  eq $C->{PATH}
  &&
  (! defined $when || $when <= $C->{EXPIRES});
}

=item Get(I<follow>)

Fetch a URL and return a B<cs::MIME> object.
If the optional flag I<follow> is set,
act on B<Redirect> responses etc.

=cut

sub Get($$)
{ my($this,$follow)=@_;

  die "UNIMPLEMENTED";
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
