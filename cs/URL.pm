#!/usr/bin/perl
#
# Code to handle URLs.
#	- Cameron Simpson <cs@zip.com.au> 11jan1996
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
use cs::HTTPS;

package cs::URL;

=head1 GENERAL FUNCTIONS

=over 4

=item get(I<url>,I<follow>)

Create a B<cs::URL> object from the I<url> supplied
and call the B<Get> method below.
If the optional argument I<follow> is true
then redirections (301 and 302 response codes)
will be followed.

=cut

sub get($;$)
{ my($url,$follow)=@_;
  $follow=0 if ! defined $follow;

  my($U)=new cs::URL $url;
  return () if ! defined $U;
  $U->Get($follow);
}

=item head(I<url>)

Create a B<cs::URL> object from the I<url> supplied
and call the B<Head> method below.

=cut

sub head($)
{ my($url)=@_;

  my($U)=new cs::URL $url;
  return () if ! defined $U;
  $U->Head();
}

=item urls(I<url>,I<results>,I<inline>)

Return all URLs reference from the page I<url>
via the hashref I<results>,
which on resturn will have URLs as the hash keys
and the title of each link as the hash value.
If the optional argument I<inline> is true,
return ``inline'' URLs
(i.e. specified by B<SRC=> and B<BACKGROUND=> attributes)
rather than references (B<HREF=>).

=cut

sub urls($$;$)
{ my($url,$urls,$inline)=@_;
  $inline=0 if ! defined $inline;

  my($U)=new cs::URL $url;
  return 0 if ! defined $U;

  $U->URLs($urls,$inline);
  1;
}

=item urlPort(I<scheme>,I<port>)

Given a I<scheme> and I<port>,
return the numeric value of I<port>.
If the I<port> parameter is omitted,
return the default port number for I<scheme>.

=cut

sub urlPort($;$)
{ my($scheme,$port)=@_;
  $scheme=uc($scheme);

  (defined $port && length $port
      ? cs::Net::portNum($port)
      : length $scheme
	  ? grep($_ eq $scheme,HTTP,FTP,GOPHER,HTTPS,NEWS,SNEWS)
		  ? cs::Net::portNum($scheme)
		  : ''
	  : '');
}

=item undot(I<url>)

Given the text of an I<url>,
remove and B<.> or B<..> components.

=cut

sub undot($)
{ local($_)=@_;

  my $pfx = "";

  if (m(^\w+://[^/]+))
  { $pfx=$&; $_=$';
  }

  # strip newlines
  s:[\r\n]+\s*::g;

  # strip /dir/../
  s:^(/*)((\.\.?/))+:/:;
  while (s|/+[^/?#]+/+\.\./+|/|)
  {}

  $_=$pfx.$_;

  s/^\s+//;
  s/\s+$//;

  $_;
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
      { my $dirpart = $base->Path();
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

  $this->{cs::URL::SCHEME}=$scheme;
  $this->{cs::URL::HOST}=lc($host);
  $this->{cs::URL::PORT}=urlPort($scheme,$port);
  $this->{cs::URL::PATH}=cs::HTTP::unhexify($path);
  $this->{cs::URL::QUERY}=$query;
  $this->{cs::URL::ANCHOR}=$anchor;

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

##sub _OldAbs($$)
##{ my($base,$target)=@_;
##  # make target into an object
##  $target=new cs::URL $target if ! ref $target;
##
####  warn "base url = ".$base->Text()."\n"
####      ."targ url = ".$target->Text()."\n";
##
##  my($abs)=bless {};
##  for (keys %$target)
##  { $abs->{$_}=$target->{$_};
##  }
##
##  # short circuit
##  return $abs if $abs->IsAbs();
##
##  ## warn "NOT ABS ".$abs->Text();
##
##  # we need an absolute URL to resolve against
##  if (! $base->IsAbs())
##  {
##    my($context)=$base->Context();
##    ## warn "context=[".$context->Text()."]";
##
##    if (! defined $context)
##    {
###	  ## warn "$::cmd: Abs(\""
###	      .$base->Text()
###	      ."\",\""
###	      .$target->Text()
###	      ."\"): no context for resolving LHS";
##      return $target;
##    }
##
##    if (! $context->IsAbs())
##    {
###	  ## warn "$::cmd: non-absolute context (\""
###	      .$context->Text()
###	      ."\") for \""
###	      .$base->Text()
###	      ."\"";
##      return $target;
##    }
##
##    ## warn "call ABS from context";
##
##    $base=$context->Abs($base);
##
##    ## warn "ABS from CONTEXT(".$context->Text().")="
##    ##	.$base->Text();
##  }
##
##  my($dodgy,$used_dodge)=(0,0);
##
##  if (! defined $abs->{cs::URL::SCHEME}
##   && defined $base->{cs::URL::SCHEME})
##  { $abs->{cs::URL::SCHEME}=$base->{cs::URL::SCHEME};
##  }
##  elsif ($abs->{cs::URL::SCHEME} ne $base->{cs::URL::SCHEME})
##  {
##    $base=$target->Context($abs->{cs::URL::SCHEME});
##
##    ## my(@c)=caller;
##    ## warn "no context for ".cs::Hier::h2a($target,1)." from [@c]"
##    ##	if ! defined $base;
##
##    return $abs if ! defined $base;
##    $dodgy=! $base->IsAbs();
##  }
##
##  if (! defined $abs->{cs::URL::HOST}
##   && defined $base->{cs::URL::HOST})
##  { $used_dodge=1;
##
##    $abs->{cs::URL::HOST}=$base->{cs::URL::HOST};
##    ## warn "set HOST to $base->{cs::URL::HOST}\n";
##
##    if (defined $base->{cs::URL::PORT})
##    { $abs->{cs::URL::PORT}=$base->{cs::URL::PORT};
##    }
##    else
##    { delete $abs->{cs::URL::PORT};
##    }
##
##    # XXX - password code?
##    if (defined $base->{USER})
##    { $abs->{USER}=$base->{USER};
##    }
##    else
##    { delete $abs->{USER};
##    }
##  }
##
##  if ($abs->{PATH} !~ m:^/:)
##  { $used_dodge=1;
##
##    my($dirpart)=$base->{PATH};
##    $dirpart =~ s:[^/]*$::;
##    $dirpart="/" if ! length $dirpart;
##
##    $abs->{PATH}="$dirpart$abs->{PATH}";
####    warn "interim path = $abs->{PATH}\n";
##  }
##
##  # trim /.
##  while ($abs->{PATH} =~ s:/+\./:/:)
##  {}
##
##  # trim leading /..
##  while ($abs->{PATH} =~ s:^/+\.\./:/:)
##  {}
##
##  # trim /foo/..
##  while ($abs->{PATH} =~ s:/+([^/]+)/+\.\./:/:)
##  {}
##
##  if ($dodgy && $used_dodge)
##  {
##    warn "$::cmd: no default for scheme \"$abs->{cs::URL::SCHEME}\",\n";
##    warn "\tusing \"".$base->Text()."\" instead, despite scheme mismatch\n";
##  }
##
####  warn "RETURNING ABS = ".cs::Hier::h2a($abs,1);
##
##  $abs;
##}

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

  my@c=caller;die "cs::URL::IsAbs() called from [@c]";
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
  $scheme=$this->{cs::URL::SCHEME} if ! defined $scheme
			  && defined $this->{cs::URL::SCHEME}
			  && length $this->{cs::URL::SCHEME};

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
  my $SC=$this->{cs::URL::SCHEME};
  $url=lc($SC).":" if length $SC;
  if ($SC eq FILE || $SC eq HTTP || $SC eq HTTPS || $SC eq FTP)
  { $url.='//'.$this->HostPart() if defined $this->{cs::URL::HOST};
  }
  $url.=$this->LocalPart($noanchor);

  ## warn "text=$url\n";

  $url;
}

=item Scheme()

Return the scheme name for this URL.

=cut

sub Scheme($)
{ shift->{cs::URL::SCHEME};
}

=item Host()

Return the host name for this URL.

=cut

sub Host($)
{ shift->{cs::URL::HOST};
}

=item Port()

Return the port number for this URL.

=cut

sub Port($)
{ shift->{cs::URL::PORT};
}

=item Path()

Return the path component of the URL.

=cut

sub Path($)
{ shift->{cs::URL::PATH};
}

=item Query()

Return the query_string component of the URL.

=cut

sub Query($)
{ shift->{cs::URL::QUERY};
}

=item Anchor()

Return the anchor component of the URL.

=cut

sub Anchor($)
{ shift->{cs::URL::ANCHOR};
}

=item HostPart()

Return the B<I<user>@I<host>:I<port>> part of the URL.

=cut

sub HostPart($)
{ my($this)=@_;

  return "" if ! defined $this->{cs::URL::HOST};

  my($hp);

  $hp='';
  $hp.="$this->{USER}\@" if defined $this->{USER};
  $hp.=lc($this->{cs::URL::HOST}) if defined $this->{cs::URL::HOST};
  $hp.=":".lc($this->{cs::URL::PORT}) if defined $this->{cs::URL::PORT}
			      && $this->{cs::URL::PORT}
			      ne urlPort($this->{cs::URL::SCHEME});

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

  my $l = $this->{cs::URL::PATH};

  my $q = $this->Query();
  if (defined $q && length $q)
  { $l.="?$q"; }

  if (! $noanchor)
  { my $a = $this->Anchor();
    if (defined $a && length $a)
    { $l.="#$a";
    }
  }

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

  substr(lc($this->{cs::URL::HOST}),-length($C->{DOMAIN}))
  eq $C->{DOMAIN}
  &&
  substr($this->{cs::URL::PATH},0,length($C->{PATH}))
  eq $C->{PATH}
  &&
  (! defined $when || $when <= $C->{EXPIRES});
}

=item Get(I<follow>)

Fetch a URL and return a B<cs::MIME> object.
If the optional flag I<follow> is set,
act on B<Redirect> responses etc.
Returns a tuple of (I<endurl>,I<rversion>,I<rcode>,I<rtext>,I<MIME-object>)
where I<endurl> is the URL object whose data was eventually retrieved
and I<MIME-object> is a B<cs::MIME> object
or an empty array on error.

=cut

sub Get($;$)
{ my($this,$follow)=@_;
  $follow=0 if ! defined $follow;

  local(%cs::URL::_Getting);

  $this->_Get($follow);
}

sub _Get($$)
{ my($this,$follow)=@_;
  $follow=0 if ! defined $follow;

  my($url,$context);

  my %triedAuth;

  GET:
  while (1)
  { $url = $this->Text(1);
    $context="$::cmd: GET $url";

    if ($cs::URL::_Getting{$url})
    { warn "$context:\n\tredirection loop detected";
      last GET;
    }

    $cs::URL::_Getting{$url}=1;

    my $scheme = $this->Scheme();
    if (! grep($_ eq $scheme, HTTP, FTP,HTTPS))
    { warn "$context:\n\tscheme $scheme not implemented";
      last GET;
    }

    my $rqhdrs = cs::HTTP::rqhdr($this);

    my ($phost,$pport) = $this->Proxy();

    my $phttp = ( $scheme eq HTTPS
		? new cs::HTTPS ($this->Host(), $this->Port())
		: new cs::HTTP ($phost,$pport,1)
		);

    if (! defined $phttp)
    { warn "$context:\n\tcan't connect to proxy server $phost:$pport: $!";
      last GET;
    }

    my($rversion,$rcode,$rtext,$M);

    warn "GET $url\n" if $::Verbose;
    ($rversion,$rcode,$rtext,$M)=$phttp->Request(GET,$url,$rqhdrs);

    if (! defined $rversion)
    { warn "$context: nothing from proxy";
      last GET;
    }

    if ($rcode eq $cs::HTTP::M_MOVED || $rcode eq $cs::HTTP::M_FOUND)
    {
      $ENV{HTTP_REFERER}=$url;
      my $newurl=$M->Hdr(LOCATION);
      chomp($newurl);
      $newurl =~ s/^\s+//g;
      $newurl =~ s/\s+$//g;

      warn "REDIRECT($rcode) to $newurl\n" if $::Verbose;

      $this = new cs::URL($newurl,$this);
      if (! defined $this)
      { warn "$context:\n\tcan't parse URL \"$newurl\"";
	last GET;
      }
    }
    elsif ($rcode eq $cs::HTTP::E_UNAUTH && ! $triedAuth{$url})
    {
      my $auth = $this->AuthDB();
      if (! defined $auth)
      { warn "$context:\n\tauthentication challenge but no auth db";
	last GET;
      }

      # get challenge info from hdrs
      my ($scheme,$label)=$auth->ParseWWW_AUTHENTICATE($M);
      if (! defined $scheme)
      { warn "$context:\n\tcan't parse WWW_AUTHENTICATE";
	last GET;
      }

      my $host = $this->Host();

      # get response info
      my $resp = $auth->GetAuth($scheme,$host,$label);
      if (! ref $resp)
      { warn "$context:\n\tno login/password for $scheme/$host/$label";
	last GET;
      }

      if ($::Verbose || $::Debug)
      { warn "$context:\n\ttrying auth $resp->{USERID}:$resp->{PASSWORD}\n";
      }

      $rqhdrs=cs::HTTP::rqhdr($this);
      $auth->HdrsAddAuth($rqhdrs,$scheme,$resp);
      $triedAuth{$url}=1;
    }
    elsif ($rcode ne $cs::HTTP::R_OK)
    { warn "$context:\n\tunexpected response: $rversion $rcode $rtext\n";
      last GET;
    }
    else
    {
      return ($this,$rversion,$rcode,$rtext,$M);
    }

    last GET if ! $follow;
  }

  return ();
}

=item Head()

Fetch a URL and return a B<cs::MIME> object.
Returns a tuple of (I<endurl>,I<rversion>,I<rcode>,I<rtext>,I<MIME-object>)
where I<endurl> is the URL object whose data was retrieved
and I<MIME-object> is a B<cs::MIME> object
or an empty array on error.

=cut

sub Head($)
{ my($this)=@_;

  my($url,$context);

  HEAD:
  while (1)
  { $url = $this->Text();
    $context="$::cmd: HEAD $url";

    my $scheme = $this->Scheme();
    if ($scheme ne HTTP && $scheme ne FTP)
    { warn "$context:\n\tscheme $scheme not implemented";
      return ();
    }

    my $rqhdrs = cs::HTTP::rqhdr($this);

    my ($phost,$pport) = $this->Proxy();

    my $phttp = new cs::HTTP ($phost,$pport,1);

    if (! defined $phttp)
    { warn "$context:\n\tcan't connect to proxy server $phost:$pport: $!";
      return ();
    }

    my($rversion,$rcode,$rtext,$M);

    warn "HEAD $url\n" if $::Verbose;
    ($rversion,$rcode,$rtext,$M)=$phttp->Request(HEAD,$url,$rqhdrs);

    if (! defined $rversion)
    { warn "$context: nothing from proxy";
      return ();
    }

    return ($this,$rversion,$rcode,$rtext,$M);
  }

  return ();
}

=item URLs(I<hashref>,I<inline>)

Return the URLs references by the page associated with the current URL.
The hash referenced by I<hashref> will be filled with URLs and titles
(from the source document - not the taregt URL's B<TITLE> tag),
using the URL for the key and the title for the value.
See the B<cs::HTML::sourceURLs> method for detail.
If the optional parameter I<inline> is true,
return the URLs of inlined components such as images.

=cut

sub URLs($$;$)
{ my($this,$urls,$inline)=@_;
  $inline=0 if ! defined $inline;

  my($endU,$rversion,$rcode,$rtext,$M)=$this->Get(1);
  return () if ! defined $endU;	# fetch failed

  my %urls;

  $this->_URLsFromMIME($inline,$M,$urls);
}

sub _URLsFromMIME($$$$)
{ my($this,$inline,$M,$urls)=@_;

  my $type = $M->Type();
  my $subtype = $M->SubType();

  if ($type eq MULTIPART)
  { my @M = $M->Parts();
    
    for my $subM (@M)
    { $this->_URLsFromMIME($inline,$subM,$urls);
    }

    return;
  }
  
  if ($type eq TEXT)
  {
    if ($subtype eq HTML)
    { my $src = $M->BodySource(1,1);
      cs::HTML::sourceURLs($urls,$src,$inline,$this);
      return;
    }
  }

  warn "$::cmd: ".$this->Text().":\n\tcan't parse [$type/$subtype]\n";
}

=item Proxy()

Return an array of (I<host>,I<port>) as the proxy to contact
for this URL.
Currently dissects the B<WEBPROXY> environment variable.

=cut

sub Proxy($)
{ my($this)=@_;

  my @proxy;

  if (defined $ENV{WEBPROXY} && length $ENV{WEBPROXY})
  { if ($ENV{WEBPROXY} =~ /:/)	{ @proxy=($`,$'); }
    else			{ @proxy=($ENV{WEBPROXY},80); }
  }

  @proxy;
}

=item AuthDB()

Return a B<cs::HTTP::Auth> object
containing the authentication tokens we possess.

=cut

sub AuthDB($)
{ my($this)=@_;

  ::need(cs::HTTP::Auth);
  cs::HTTP::Auth->new("$ENV{HOME}/private/httpauth.db");
}

=back

=head1 ENVIRONMENT

B<WEBPROXY> - the HTTP proxy service to use for requests,
of the form B<I<host>:I<port>>.

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
