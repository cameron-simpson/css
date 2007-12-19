#!/usr/bin/perl
#
# Do HTTP-related client stuff.
#	- Cameron Simpson <cs@zip.com.au>
#

=head1 NAME

cs::HTTP - operate the HTTP protocol

=head1 SYNOPSIS

use cs::HTTP;

=head1 DESCRIPTION

This module implements functions and methods for dealing with HTTP connections.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Net::TCP;
use cs::MIME;
use cs::URL;
use cs::RFC822;

package cs::HTTP;

@cs::HTTP::ISA=qw(cs::Net::TCP);

$cs::HTTP::Debug=exists $ENV{DEBUG_HTTP} && length $ENV{DEBUG_HTTP};

$cs::HTTP::Port=80;	# default port number for HTTP

%cs::HTTP::_Auth=();

$cs::HTTP::_HTTP_VERSION='HTTP/1.0';

=head1 CONSTANTS

=head2 HTTP Response Codes

=over 4

=cut

####################
# Response codes
#
#  Good ones

=item $R_OK

200 Request fulfilled.

=cut

$cs::HTTP::R_OK	='200';

=item $R_CREATED

201 follows post - text is URI of new document

=cut

$cs::HTTP::R_CREATED	='201';

=item $R_ACCEPTED

202 request accepted but not complete (may never be)

=cut

$cs::HTTP::R_ACCEPTED	='202';

=item $R_PARTIAL

203 response is a private metaweb, not the original

=cut

$cs::HTTP::R_PARTIAL	='203';

=item $R_NORESPONSE

204 request ok, but nothing to send back - client's browser should stay in the same spot

=cut

$cs::HTTP::R_NORESPONSE ='204';

=item $M_MOVED

301 document has new permanent URI.
Lines follow of the form:

	B<URI>: I<url> I<comment>

=cut

$cs::HTTP::M_MOVED	='301';

=item $M_FOUND

302 document is currently elsewhere.
Lines follow of the form:

	B<URI:> I<url> I<comment>

=cut

$cs::HTTP::M_FOUND	='302';

=item $M_METHOD

303 document needs different method.
Following is:

	B<Method:> I<method> I<url>
	I<body-section>

I<body-section> is data for I<method>.

=cut

$cs::HTTP::M_METHOD	='303';

=item $M_NOT_MOD

304 response to conditional GET - document not modified, client should used cached version.

=cut

$cs::HTTP::M_NOT_MOD	='304';

#  Bad ones - 4xx is client error, 5xx is server error

=item $E_BAD

400 bad request

=cut

$cs::HTTP::E_BAD	='400';

=item $E_UNAUTH

401 bad authorisation - text is auth scheme spec

=cut

$cs::HTTP::E_UNAUTH	='401';

=item $E_PAYMENT

402 payment required - text is payment scheme spec

=cut

$cs::HTTP::E_PAYMENT	='402';

=item $E_FORBIDDEN

403 request denied - authorisation won't help

=cut

$cs::HTTP::E_FORBIDDEN	='403';

=item $E_NOT_FOUND

404 no match for URI

=cut

$cs::HTTP::E_NOT_FOUND	='404';

=item $E_INTERNAL

500 undiagnosed server problem

=cut

$cs::HTTP::E_INTERNAL	='500';

=item $E_NOT_IMPL

501 facility not supported

=cut

$cs::HTTP::E_NOT_IMPL	='501';

=item $E_OVERLOAD

502 load too high - try later

=cut

$cs::HTTP::E_OVERLOAD	='502';

=item $E_GTIMEOUT

503 gateway timeout - subservices didn't respond in time

=cut

$cs::HTTP::E_GTIMEOUT	='503';

=back

=head1 GENERAL FUNCTIONS

=over 4

=item hexify(I<string>,I<chptn>)

Convert all characters I<string> not matching I<chptn> into B<%I<xx>> escapes.
If I<chptn> is B<HTML>, use B<[^!-~]|"> for the pattern.

=cut

sub hexify($$)
{ my($str,$hexchptn)=@_;

  if ($hexchptn eq HTML) { $hexchptn='[^!-~]|["@#]'; }

  $str =~ s/$hexchptn/sprintf("%%%02x",ord($&))/eg;

  $str;
}

=item unhexify(I<string>)

Convert all B<%I<xx>> escapes in I<string> into characters.

=cut

sub unhexify($)
{ my($str)=@_;
  $str =~ s/%([\da-f][\da-f])/chr(hex($1))/egi;
  $str;
}

$cs::HTTP::ptnToken='[^][\000-\037()<>@,;:\\"/?={}\s]+';

sub parseAttrs
{ local($_)=shift;
  my($max)=@_;

  my($h)={};

  my($attr,$value);

  #           1                           2
  while ((! defined $max || $max-- > 0)
      && /^\s*($cs::HTTP::ptnToken)\s*=\s*("[^"]*"|$cs::HTTP::ptnToken)(\s*;)?/o)
	{ ($attr,$value)=($1,$2);
	  $_=$';

	  $attr=uc($attr);
	  $value =~ s/^"(.*)"$/$1/;

	  $h->{$attr}=$value;
	}

  wantarray ? ($h,$_) : $h;
}

sub _url2hpf
{ my($url)=@_;

  main::need(cs::URL);

  my($URL)=new cs::URL $url;

  return undef unless $URL->Proto() eq HTTP;

  ($URL->Hopst(),$URL->Proto(),$URL->Proto());
}

#######################
# Grammar routines.
#

$cs::HTTP::_RangeCTL='\000-\031\177';
$cs::HTTP::_RangeTSpecial="()<>@,;:\\\"{} \t";

$cs::HTTP::_PtnToken="[^$cs::HTTP::_RangeCTL$cs::HTTP::_RangeTSpecial]+";

$cs::HTTP::_PtnLWS='(\r?\n)?[ \t]+';
$cs::HTTP::_PtnQDText="(($cs::HTTP::_PtnLWS)|[^\"$cs::HTTP::_RangeCTL])";

$cs::HTTP::_PtnQuotedString="\"($cs::HTTP::_PtnQDText)*\"";

sub token
{ local($_)=@_;

  return undef unless /\s*($cs::HTTP::_PtnToken)\s*/o;

  wantarray ? ($1,$') : $1;
}

sub quoted_string
{ local($_)=shift;
  my($keep_quotes)=@_;

  return undef unless /\s*($cs::HTTP::_PtnQuotedString)\s*/o;

  my($match,$tail)=($1,$');

  $match=$1 if ! $keep_quotes && $match =~ /^"(($cs::HTTP::_PtnQDText)*)/o;

  wantarray ? ($match,$tail) : $match;
}

sub word
{ local($_)=shift;
  my($keep_quotes)=@_;

  return undef unless /\s*(($cs::HTTP::_PtnToken)|($cs::HTTP::_PtnQuotedString))\s*/o;

  my($match,$tail)=($1,$');

  $match=$1 if ! $keep_quotes && $match =~ /^"(($cs::HTTP::_PtnQDText)*)/o;

  wantarray ? ($match,$tail) : $match;
}

sub tokenList	# text -> ([tok,val,...],tail)
{ local($_)=@_;
  my($list,$tail);
  my($tok,$value);

  $list=[];

  while (( ($tok,$tail)=token($_) )
      && $tail =~ /^=\s*/
      && ( ($value,$tail)=quoted_string($') )
	)
	{ push(@$list,$tok,$value);
	  $_=$tail;
	  last TOKEN unless /^,\s*/;
	  $_=$';
	}

  wantarray ? ($list,$_) : $list;
}

=item rqhdr(I<url>,I<srcurl>,I<agent>)

Return a B<cs::RFC822> header set
to accompany a request for the B<cs::URL> I<url>,
including cookie fields.
The optional B<cs::URL> I<srcurl> is used for the B<Referer:> field,
defaulting to the environment variable B<HTTP_REFERER>
or failing that, I<url>.
The optional I<agent> is used for the B<User-Agent:> field,
defaulting to the environment variable B<HTTP_USER_AGENT>
or failing that, "B<$::cmd/1.0>".

=cut

sub rqhdr($;$$)
{ my($U,$srcURL,$agent)=@_;
  $srcURL=defined($ENV{HTTP_REFERER}) ? $ENV{HTTP_REFERER} : $U->Text()
	if ! defined $srcURL;

  $agent=defined($ENV{HTTP_USER_AGENT}) ? $ENV{HTTP_USER_AGENT} : "$::cmd/1.0"
	if ! defined $agent && ! length $agent;

  my $rqhdrs = new cs::RFC822;

  $rqhdrs->Add([ACCEPT_ENCODING,(defined($ENV{HTTP_ACCEPT}) ? $ENV{HTTP_ACCEPT} : "identity")]);
  $rqhdrs->Add([REFERER,$srcURL]);
  $rqhdrs->Add([HOST,$U->Host()]);
  $rqhdrs->Add([USER_AGENT,$agent]);

  @::Cookies=getCookies() if ! @::Cookies;

  my $cookieline = "";
  for my $C (@::Cookies)
  { ## warn "check ".cs::Hier::h2a($C,0);
    if ($U->MatchesCookie($C))	## $::Now
    { $cookieline.="; " if length $cookieline;
      $cookieline.="$C->{NAME}=$C->{VALUE}";
    }
  }

  if (length $cookieline)
  { $rqhdrs->Add("Cookie: $cookieline");
    ## warn "rqhdrs=".cs::Hier::h2a($rqhdrs,1);
  }

  $rqhdrs;
}

=item getCookies(I<cookiefile>)

Return an array of hashrefs representing cookies
from the specified file.
If I<cookiefile> is omitted
use the file specified by the environment variable B<$COOKIE_FILE>
or failing that B<$HOME/.netscape/cookies.txt>.

=cut

sub getCookies()
{ my($file)=@_;
  if (! defined $file)
  { $file = (exists $ENV{COOKIE_FILE} && $ENV{COOKIE_FILE} =~ /\.txt$/
	  ? $file=$ENV{COOKIE_FILE}
	  : "$ENV{HOME}/.netscape/cookies.txt"
	  ;
  }

  my $C = cs::Source::open($file);
  return () if ! defined $C;

  my @C;

  my $lineno = 0;
  local($_);

  COOKIELINE:
  while (defined($_=$C->GetLine()) && length)
  { $lineno++;

    chomp;
    s/^\s+//;
    next COOKIELINE if !length || /^#/;

    my (@f) = split(/\t/);
    if (@f != 7 && @f != 6)
    { warn "$::cmd: $file, line $lineno: wrong # of fields\n";
      next COOKIELINE;
    }

    my($domain,$bool1,$pathpfx,$bool2,$expires,$name,$value)=@f;
    $value="" if ! defined $value;

    setCookie(\@C,$domain,$bool1 eq TRUE,$pathpfx,$bool2 eq TRUE,$expires,$name,$value);
    push(@C, { DOMAIN => $domain,
	       BOOL1 => ($bool1 eq TRUE),
	       PATH => $pathpfx,
	       BOOL2 => ($bool2 eq TRUE),
	       EXPIRES => $expires+0,
	       NAME => $name,
	       VALUE => $value,
	     });
  }

  return @C;
}

sub setCookie($$$$$$$$)
{ my($CA,$domain,$bool1,$pathpfx,$bool2,$expires,$name,$value)=@_;

  ## warn "setCookie($domain$pathpfx: $name=$value\n";
  push(@$CA, { DOMAIN => $domain,
	       BOOL1 => $bool1,
	       PATH => $pathpfx,
	       BOOL2 => $bool2,
	       EXPIRES => $expires,
	       NAME => $name,
	       VALUE => $value,
	     });
}

sub applyCookies($$$)
{ my($CA,$M,$U)=@_;

  local($_);

  for my $H ($M->Hdrs())
  {
    if ($H =~ /^set-cookie:\s*/i)
    { $_=$';
      my($domain,$bool1,$path,$bool2,$expire_spec,$name,$value);
      $domain=$U->Host();
      if (/^([a-z_][_\w]*)=([^;\s]*)/)
      { ($name,$value)=($1,$2);
	$_=$';
	my($p);
	($p,$_)=cs::MIME::parseTypeParams($_);
	$domain=$p->{'domain'} if exists $p->{'domain'};
	$path=$p->{'path'} if exists $p->{'path'};
	setCookie(\@::Cookies,$domain,$bool1,$path,$bool2,0,$name,$value);
      }
    }
  }
}

#######################
# Authorisation code.
#

@cs::HTTP::_Auth=();
sub addAuthority
{ my($host,$port,$pathpfx)=@_;

  push(@$cs::HTTP::_Auth,
	{ HOST => $host, PORT => $port, PATH => $pathpfx });
}

sub findAuthority
{ my($host,$port,$path)=@_;
  my($ref);

  for my $A (@cs::HTTP::_Auth)
  { if ($host eq $A->{HOST}
     && $port == $A->{PORT}
     && length($path) >= length $A->{PATH}
     && substr($path,$[,length($A->{PATH})) eq $A->{PATH}
     && ( ! defined $ref
       || length $ref->{PATH} < length $A->{PATH}
	)
       )
    { $ref=$A;
    }
  }

  $ref;
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::HTTP (I<host>, I<port>, I<isProxy>)

Return a B<cs::Net::TCP> object
attached to the B<I<host>:I<port>> specified
(I<port> defaults to 80, the standard HTTP port).
If the optional I<isProxy> flag is set
the connection is taken to be to a web proxy.

=cut

sub new($$;$$)
{ my($class,$host,$port,$isProxy)=@_;
  $port=$cs::HTTP::Port if ! defined $port;
  $isProxy=0 if ! defined $isProxy;

  my $this;

  ## warn "HTTP: calling new TCP($host,$port)";
  $this=new cs::Net::TCP ($host,$port);
  return undef if ! defined $this;

  $this->{cs::HTTP::HOST}=lc($host);
  $this->{cs::HTTP::PORT}=$port;
  $this->{cs::HTTP::ISPROXY}=$isProxy;

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub DESTROY
{ my($this)=@_;
  $this->SUPER::DESTROY($this);
}

=item Request(I<method>,I<uri>,I<hdrs>,I<data>,I<version>,I<sinkfile>)

Request the I<uri> with the specified I<method>.
If supplied, I<hdrs> are added to the headers sent with the request,
superceding matching headers.
If supplied, I<data> is a B<cs::Source> containing data
to follow the headers.
If supplied, I<version> is an B<HTTP/I<n>.I<n>> version
string to use instead of the default B<HTTP/1.0>.

=cut

sub Request($$;$$$)
{ my($this,$method,$uri,$hdrs,$data,$version,$sinkfile)=@_;
  $method=uc($method);
  die "no \$uri" if ! defined $uri;
  $version=$cs::HTTP::_HTTP_VERSION if ! defined $version;

  my $keepsink=(defined $sinkfile ? 1 : 0);

  my($olduri);

  if (ref $uri)
  { ($uri,$olduri)=@$uri;
  }
  elsif (defined $ENV{HTTP_REFERER})
  { $olduri=$ENV{HTTP_REFERER};
  }
  else
  { $olduri=$uri;
  }

  # minor cleans - XXX should do something more thorough
  $uri=hexify($uri,HTML);
  my($U)=new cs::URL $uri;

  my($rqhdrs)=rqhdr($U,$olduri);
  if (defined $hdrs)
  { for ($hdrs->Hdrs())
    { $rqhdrs->Add($_,SUPERCEDE);
    }
  }

  my $rquri = $uri;
  $rquri =~ s:\s:sprintf("%%%02x",ord($&)):eg;

  ############################
  # Supply request and headers.
  $this->Put("$method "
	    .($this->{cs::HTTP::ISPROXY}
		? $rquri
		: $U->LocalPart())
	    ." $version\r\n");
  $cs::HTTP::Debug && warn "HTTP: $method $rquri $version";

  $rqhdrs->WriteItem($this->Sink());
  $cs::HTTP::Debug && warn "HTTP: ".cs::Hier::h2a($rqhdrs,1);

  local($_);

  ############################
  # Supply data if present.
  if (defined $data)
  {
    while (defined ($_=$data->Read()) && length)
    { $this->Put($_);
    }
  }

  $this->Flush();

  ############################
  # Collect response.
  if (! defined ($_=$this->GetLine()) || ! length)
  {
    warn "EOF from HTTP server";
    return ();
  }

  chomp;	s/\r$//;
  if (! /^(http\/\d+\.\d+)\s+(\d{3})\s*/i)
  {
    warn "bad response from HTTP server: $_";
    return ();
  }

  my($rversion,$rcode,$rtext)=($1,$2,$');
  
  my $M = new cs::MIME ($this->Source(),1,$sinkfile,$keepsink);

  applyCookies(\@::Cookies,$M,$U);

  wantarray
  ? ($rversion,$rcode,$rtext,$M)
  : { VERSION => $rversion,
      CODE    => $rcode,
      TEXT    => $rtext,
      MESSAGE => $M,
    }
  ;
}

=item Get(I<args...>)

Call the B<Request> method a B<GET> method specification.

=cut

sub Get
{ my($this)=shift;
  $this->Request(GET,@_);
}

=item Post(I<uri>,I<data>)

Call the B<Request> method for the specified I<uri>
with a B<POST> method specification
and the values in the hashref I<data>
attached as the data source.

=cut

sub Post
{ my($this,$uri,$data)=(shift,shift,shift);

  my(@data);
  for (keys %$data)
  {
    push(@data,"$_=".urlEncode($data->{$_})."\n");
  }

  my $d = new cs::Source (ARRAY,\@data);
  die "can't make cs::Source(ARRAY,@data)" if ! defined $d;

  $this->Request(POST,$uri,undef,$d);
}

sub RequestData
{ my($this)=shift;
  my($rversion,$rcode,$rtext,$hdrs)=$this->Request(@_);

  return undef if ! defined $rversion || $rcode ne 200;

  my($cte)=$hdrs->Hdr(CONTENT_TRANSFER_ENCODING);

  defined $cte && length $cte
	? cs::MIME::decodedSource($this->Source(),$cte)
	: $this->Source();
}

=back

=head1 SEE ALSO

cs::Net::TCP(3), cs::Port(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
