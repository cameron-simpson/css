#!/usr/bin/perl
#
# MIME - Multipurpose Internet Mail Extension
#
# Handle MIME messages.
# See RFCs:
#   822 - Internet Mail Messages
#  1344	- Implications of MIME for Internet Mail Gateways
#  1437	- The Extension of MIME Content-Types to a New Medium
#  1521	- Media Type Registration Procedure
#
#	- Cameron Simpson <cs@zip.com.au> 17may96
#

=head1 NAME

cs::MIME - handle Multipurpose Internet Mail Extension data

=head1 SYNOPSIS

use cs::MIME;

=head1 DESCRIPTION

This module implements methods
for dealing with MIME data.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::ALL;
use cs::RFC822;
use cs::Source;
use cs::SubSource;
use cs::CacheSource;
use cs::MIME::Base64;
use cs::MIME::QuotedPrintable;

package cs::MIME;	# cs::ALL::useAll();

$cs::MIME::_Range_tspecials='()<>@,;:\\"\/\[\]?.=';

=head1 GENERAL FUNCTIONS

=over 4

=item parseContentType(I<content-type>)

Parse a B<Content-Type> line I<content-type>,
returning a tuple of (I<typ>,I<subtype>,I<params>,I<unparsed>)
being the type and subtype,
a hashref containing any parameters on the line
and any remaining data which could not be parsed.

=cut

sub parseContentType($)
{ local($_)=@_;

  my($params)={};
  my($type,$subtype);

  if (m:^\s*([-\w]+)\s*/\s*([-\w]+)\s*:)
  { $type=uc($1);
    $subtype=uc($2);
    $_=$';

    ($_,$params)=parseTypeParams($_);
  }
  else
  { $type=TEXT;
    $subtype=PLAIN;
  }

  ($type,$subtype,$params,$_);
}

=item parseTypeParams(I<parameter-string>)

Parse a the parameters section of a B<Content-Type> line
returning a tuple of (I<params>,I<unparsed>)
a hashref containing any parameters on the line
and any remaining data which could not be parsed.

=cut

sub parseTypeParams($)
{ local($_)=@_;

  my($params)={};
  my($param,$value);

  s/^\s*(;+\s*)+//;
  PARAM:
  while (length)
  { last PARAM unless /^([-\w]+)\s*=\s*/;

    $param=$1; $_=$';

    $param=uc($param);
    $param =~ s/-/_/g;

    if (/^"((\\.|[^\\"])*)"\s*/)
    { $value=$1; $_=$';
      $value =~ s/\\(.)/$1/g;
    }
    else
    { /^([^\s$cs::MIME::_Range_tspecials]*)\s*/;
      $value=$1; $_=$';
    }

    $params->{$param}=$value;

    s/^\s*(;+\s*)+//;
  }

  ($params,$_);
}

=item decodedSource(I<source>, I<cte>, I<istext>)

Return a new B<cs::Source>
containing the decoded content of the supplied B<cs::Source> I<source>
according to the supplied B<Content-Transfer-Encoding> string I<cte>
and optional I<istext> flag.
I<istext> defaults to false for B<BASE64> data and true for B<QUOTED-PRINTABLE> data.

=cut

sub decodedSource($$;$)
{ my($s,$cte,$isText)=@_;
  $cte=uc($cte);

  if ($cte eq '8BIT' || $cte eq '7BIT' || $cte eq 'BINARY')
  # recognised null-encodings
  { }
  elsif ($cte eq BASE64)
  { $isText=0 if ! defined $isText;
    $s=new cs::MIME::Base64 (Decode, $s, $isText);
  }
  elsif ($cte eq 'QUOTED-PRINTABLE')
  { $isText=1 if ! defined $isText;
    $s=new cs::MIME::QuotedPrintable (Decode, $s, $isText);
  }
  else
  { warn "can't decode Content-Transfer-Encoding \"$cte\"";
    return undef;
  }

  $s;
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::MIME I<source>, I<usecsize>

Create a MIME object.
If I<source> (a file pathname or a B<cs::Source>)
is supplied then its content is copied
and the headers extracted, leaving the I<source>
positioned just past the end of the body.
To avoid this cost, call with no B<cs::Source>
and use the B<UseHdrs> method to place header infomation
into the object.
The optional I<usecsize> parameter
says to trust the B<Content-Size> header if present,
placing a limit on the data read from the I<source>.
The default is to ignore it.

=cut

sub new($;$$)
{ my($class,$s,$usecsize)=@_;
  $usecsize=0 if ! defined $usecsize;

  my($this)=bless {
	      HDRS => new cs::RFC822,
	      TYPE	=> TEXT,	# text/plain by default
	      SUBTYPE	=> PLAIN,
	      TYPEPARAMS=> {},
	      HDRS	=> new cs::RFC822,
	      CTE	=> '8BIT',	# Content-Transfer-Encoding
	    }, $class;

  if (defined $s)
  { $this->UseSource($s,$usecsize);
  }

  $this;
}

=back

=head1 OBJECT METHODS

=over 4

=item UseSource(I<source>,I<usecsize>)

Read headers and body from the supplied I<source>.
The optional I<usecsize> parameter
says to trust the B<Content-Size> header if present,
placing a limit on the data read from the I<source>.
The default is to ignore it.

=cut

sub UseSource($$;$)
{ my($this,$s,$usecsize)=@_;
  $usecsize=0 if ! defined $usecsize;

  $s=new cs::Source (PATH, $s) if ! ref $s;
  return undef if ! defined $s;

  my $H = $this->Hdrs();
  $H->SourceExtract($s);
  $this->UseHdrs($H);

  # Content-Size - push limit onto stream
  if ($usecsize
   && defined ($_=$H->Hdr(CONTENT_SIZE))
   && /\d+/)
  { my($size)=$&+0;
    $s=new cs::SubSource ($s, $s->Tell(), $size);
    return undef if ! defined $s;
  }

  # suck up the stream
  # this way original stream is at a well defined spot
  # (just past the message) regardless of whether the
  # caller asks for the body
  $this->{BODY}=$s->Get();

  # Content-Transfer-Encoding
  if (defined $s
   && defined ($_=$H->Hdr(CONTENT_TRANSFER_ENCODING,1))
   && length)
  { s/^\s+//;
    s/\s+$//;
    $this->{CTE}=uc($_);
  }
}

=item Hdrs()

Return the B<cs::RFC822> object
storing the headers of the MIME object.

=cut

sub Hdrs($)	{ shift->{HDRS} }

=item Type()

Return the B<type> component of the object's B<Content-Type>.

=cut

sub Type($)	{ shift->{TYPE}; }

=item SubType()

Return the B<subtype> component of the object's B<Content-Type>.

=cut

sub SubType($)	{ shift->{SUBTYPE}; }

=item UseHdrs(I<hdrs>)

Incorporate the headers from the B<cs::RFC822> object I<hdrs>.

=cut

sub UseHdrs($$)
{ my($this,$H)=@_;

  $this->{HDRS}=$H;

  local($_);

  $_=$H->Hdr(CONTENT_TYPE);
  if (! defined || ! length)
  # old-style messages
  { $_='text/plain; charset=us-ascii';
  }

  # Content-Type
  ($this->{TYPE},$this->{SUBTYPE},$this->{TYPEPARAMS},$_)
	=parseContentType($_);
}

=item Body(I<decoded>,I<istext>)

Return a string
containing the body of this object,
decoded if I<decoded> is true (the default is false).
The optional parameter I<istext> is used as in the B<decodedSource()> function.

=cut

sub Body($;$)
{ my($this,$decoded)=@_;
  $decoded=0 if ! defined $decoded;

  return $this->{BODY} if ! $decoded;

  my $s = $this->BodySource($decoded);
  return undef if ! defined $s;

  $s->Get();
}

=item BodySource(I<decoded>,I<istext>)

Return a new B<cs::Source>
containing the body of this object,
decoded if I<decoded> is true (the default is false).
The optional parameter I<istext> is used as in the B<decodedSource()> function.

=cut

sub BodySource($;$$)
{ my($this,$decoded)=(shift,shift);
  $decoded=0 if ! defined $decoded;

  my $s = new cs::Source (SCALAR, $this->Body());
  return undef if ! defined $s;
  return $s if ! $decoded;

  decodedSource($s,$this->{CTE},@_);
}

=item ContentType(I<noparams>)

Return the body for the B<Content-Type> header.
If the optional I<noparams> is true,
suppress the "B<;I<param>=I<value>>" suffices.


=cut

sub ContentType($;$)
{ my($this,$noParams)=@_;
  $noParams=0 if ! defined $noParams;

  my($cte)=$this->{TYPE}.'/'.$this->{SUBTYPE};
  if (! $noParams)
  { my($value);
    for my $param (sort keys %{$this->{TYPEPARAMS}})
    { $value=$this->{TYPEPARAMS}->{$param};
      $param=lc($param);
      $param =~ s/_/-/g;

      $cte.="; $param=\"$value\"";
    }
  }

  $cte;
}

sub WriteItem($$$;$$$)	# (this,sink,\@cs::MIME,pre,post,sep)
{ my($this,$sink,$mlist,$pre,$post,$sep)=@_;
  $pre='' if ! defined $pre;
  $post='' if ! defined $post;
  $sep=$this->{ATTRS}->{BOUNDARY} if ! defined $sep;

  if (! length $sep)
  { ::need(cs::Date);
    $sep="cs::MIME::".cs::Date::timecode(time,0)."::GMT"
  }

  $this->Hdrs()->WriteItem($sink,$pre);

  for my $M (@$mlist)
  {
    $sink->Put("\r\n--$sep\r\n");

    $M->Hdrs()->Del(CONTENT_LENGTH);	# just in case
    $M->Hdrs()->WriteItem($sink);

    $sink->Put($M->Body());
  }

  $sink->Put("\r\n--$sep--\r\n");
  $sink->Put($post);
}

=item Parts()

Collect pretext, parts and posttext
from the object
(presumably multipart)
and return an array of strings
containing (pretext,posttext,parts...).

=cut

sub Parts()
{ my($this)=@_;

  ###### input stream
  my($s)=$this->BodySource();

  ###### results
  my $pre = '';
  my $post = '';
  my @parts;

  my($boundary)='--'.$this->{TYPEPARAMS}->{BOUNDARY};
  my($terminate)=$boundary.'--';

  my($line,$bound,$last,$crlf,$ncrlf,$first);
  my($cache);
  local($_);

  $crlf='';
  $first=1;
  $last=0;

  PART:
    while (defined($_=$s->GetLine()) && length)
    { ($line=$_) =~ s/\r?\n$//;
      $ncrlf=$&;

      # check for end of part
      if ($line eq $boundary)	{ $bound=1; $last=0; }
      elsif ($line eq $terminate){ $bound=1; $last=1; }
      else			{ $bound=0; }

      if (! $bound)
      { if ($last)		{ $post.=$_; }
	elsif (defined $cache)	{ $cache.=$_; }
	else			{ $pre.=$_; }
      }
      else
      # boundary line
      { 
	if (defined $cache)
	{ push(@parts, $cache);
	}

	$cache='';
	undef $ncrlf;	# otherwise it leaks into the next part
      }

      $crlf=$ncrlf;
    }

  ($pre,$post,@parts);
}

=back

=head1 SEE ALSO

cs::RFC822(3), cs::Source(3)

RFCs:
822 - Internet Mail Messages,
1344 - Implications of MIME for Internet Mail Gateways,
1437 - The Extension of MIME Content-Types to a New Medium,
1521 - Media Type Registration Procedure

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
