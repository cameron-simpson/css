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
#	- Cameron Simpson <cs@zip.com.au> 17may1996
#

=head1 NAME

cs::MIME - handle Multipurpose Internet Mail Extension data

=head1 SYNOPSIS

use cs::MIME;

=head1 DESCRIPTION

This module implements methods
for dealing with MIME data.
It is a subclass of the cs::RFC822(3) module.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::ALL;
use cs::RFC822;
use cs::Source;
use cs::Sink;
use cs::SubSource;
use cs::CacheSource;
use cs::MIME::Base64;
use cs::MIME::QuotedPrintable;

package cs::MIME;	# cs::ALL::useAll();

@cs::MIME::ISA=cs::RFC822;

$cs::MIME::_Range_tspecials='()<>@,;:\\"\/\[\]?.=';

=head1 GENERAL FUNCTIONS

=over 4

=item parseContentType(I<content-type>)

Parse a B<Content-Type> line I<content-type>,
returning a tuple of (I<type>,I<subtype>,I<params>,I<unparsed>)
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

    ($params,$_)=parseTypeParams($_);
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

=item decodedSource(I<source>, I<enc>, I<istext>)

Return a new B<cs::Source>
containing the decoded content of the supplied B<cs::Source> I<source>
according to the supplied encoding string I<enc>
(as for the C<Content-Encoding> or C<Content-Transfer-Encoding> headers)
and optional I<istext> flag.
I<istext> defaults to false for B<BASE64> data and true for B<QUOTED-PRINTABLE> data.

=cut

sub decodedSource($$;$)
{ my($s,$enc,$isText)=@_;
  $enc=uc($enc);

  ##warn "decodedSource: enc=[$enc], istext=[$isText]";
  if ($enc eq '8BIT' || $enc eq '7BIT' || $enc eq 'BINARY' || $enc eq 'IDENTITY')
  # recognised null-encodings
  { }
  elsif ($enc eq BASE64)
  { $isText=0 if ! defined $isText;
    $s=new cs::MIME::Base64 (Decode, $s, $isText);
  }
  elsif ($enc eq 'QUOTED-PRINTABLE')
  { $isText=1 if ! defined $isText;
    $s=new cs::MIME::QuotedPrintable (Decode, $s, $isText);
  }
  elsif ($enc eq 'GZIP')
  { $s=$s->PipeThru("exec gunzip");
  }
  else
  { warn "$0: cs::MIME::decodedSource: can't decode encoding \"$enc\"";
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

sub new($;$$$)
{ my($class,$s,$usecsize,$sinkfile,$keepsink)=@_;
  $usecsize=0 if ! defined $usecsize;
  $keepsink=0 if ! defined $keepsink;

  my $this = new cs::RFC822;
  $this->{cs::MIME::TYPE}=TEXT;		# text/plain by default
  $this->{cs::MIME::SUBTYPE}=PLAIN;
  $this->{cs::MIME::TYPEPARAMS}={};
  $this->{cs::MIME::CTE}='8BIT';	# Content-Transfer-Encoding
  $this->{cs::MIME::CE}='8BIT';		# Content-Encoding

  bless $this, $class;

  if (defined $s)
  { $this->UseSource($s,$usecsize,$sinkfile,$keepsink);
  }

  $this;
}

sub DESTROY
{ my($this)=@_;

  if (! ref $this->{cs::MIME::BODY} && ! $this->{cs::MIME::BODY_KEEP})
  { unlink($this->{cs::MIME::BODY})
	|| warn "$0: cs::MIME::DESTROY: unlink($this->{cs::MIME::BODY}): $!";
  }

  $this->SUPER::DESTROY();
}

sub _TypeParams($) { shift->{cs::MIME::TYPEPARAMS}; }
sub _Cte($) { shift->{cs::MIME::CTE}; }
sub _Ce($) { shift->{cs::MIME::CE}; }

# return a cs::Source attached to the undecoded body
sub _BodySource($)
{ my($this)=@_;

  my $_body = $this->{cs::MIME::BODY};
  if (ref $_body)
  { my $copy = $$_body;
    return new cs::Source(SCALAR,\$copy);
  }

  return new cs::Source(PATH,$_body);
}

=back

=head1 OBJECT METHODS

=over 4

=item Type()

Return the B<type> component of the object's B<Content-Type>.

=cut

sub Type($)	{ shift->{cs::MIME::TYPE}; }

=item SubType()

Return the B<subtype> component of the object's B<Content-Type>.

=cut

sub SubType($)	{ shift->{cs::MIME::SUBTYPE}; }

=item UseHdrs(I<hdrs>)

Incorporate the headers from the B<cs::RFC822> object I<hdrs>.

=cut

sub UseHdrs($$)
{ my($this,$H)=@_;

  for my $hdr (@{$H->Hdrs()})
  { $this->Add($hdr);
  }

  _ReSync($this);
}

sub _ReSync($)
{ my($this)=@_;

  local($_);

  $_=$this->Hdr(CONTENT_TYPE);
  if (! defined || ! length)
  # old-style messages
  { $_='text/plain; charset=us-ascii';
  }

  # Content-Type
  ($this->{cs::MIME::TYPE},
   $this->{cs::MIME::SUBTYPE},
   $this->{cs::MIME::TYPEPARAMS},
   $_
  )
  =parseContentType($_);

  # Content-Encoding
  if (defined ($_=$this->Hdr(CONTENT_ENCODING,1))
   && length)
  { s/^\s+//;
    s/\s+$//;
    $this->{cs::MIME::CE}=uc($_);
  }

  # Content-Transfer-Encoding
  if (defined ($_=$this->Hdr(CONTENT_TRANSFER_ENCODING,1))
   && length)
  { s/^\s+//;
    s/\s+$//;
    $this->{cs::MIME::CTE}=uc($_);
  }
}

=item UseSource(I<source>,I<usecsize>,I<sinkfile>,I<keepsink>)

Read headers and body from the supplied I<source>.
The optional I<usecsize> parameter
says to trust the B<Content-Size> header if present,
placing a limit on the data read from the I<source>.
The default is to ignore it.
Supplying the optional I<sinkfile> parameter
causes UseSource() to store the I<source> in the named file I<sinkfile>
instead of memory.
The optional parameter I<keepsink> specifies whether the I<sinkfile>
is to be unlinked on object destruction.

=cut

sub UseSource($$;$$$)
{ my($this,$s,$usecsize,$sinkfile,$keepsink)=@_;
  $usecsize=0 if ! defined $usecsize;
  $keepsink=0 if ! defined $keepsink;

  $s=new cs::Source (PATH, $s) if ! ref $s;
  return undef if ! defined $s;

  $this->SourceExtract($s);
  _ReSync($this);

  # Content-Size - push limit onto stream
  if ($usecsize
   && defined ($_=$this->Hdr(CONTENT_SIZE))
   && /\d+/)
  { my($size)=$&+0;
    $s=new cs::SubSource ($s, $s->Tell(), $size);
    return undef if ! defined $s;
  }

  # suck up the stream
  # this way original stream is at a well defined spot
  # (just past the message) regardless of whether the
  # caller asks for the body
  my $scalar;
  
  { my $sink;

    if (defined $sinkfile)
    { $sink = new cs::Sink(PATH,$sinkfile);
      die "$0: cs::MIME::UseSource(sinkfile=\"$sinkfile\"): can't open: $!"
	  if ! defined $sink;
    }
    else
    { $scalar = '';
      $sink = new cs::Sink(SCALAR,\$scalar);
    }

    local($_);

    while (defined($_=$s->Read()) && length)
    { $sink->Put($_);
    }
  }

  if (defined $sinkfile)
  { $this->{cs::MIME::BODY}=$sinkfile;
    $this->{cs::MIME::BODY_KEEP}=$keepsink;
  }
  else
  { $this->{cs::MIME::BODY}=\$scalar;
  }

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

  $this->BodySource($decoded)->Get();
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

  my $s = $this->_BodySource();
  return $s if ! $decoded;

  return undef if ! defined $s;

  $s=decodedSource($s,$this->_Cte(),@_);
  $s=decodedSource($s,$this->_Ce(),@_);

  return $s;
}

=item ContentType(I<noparams>)

Return the body for the B<Content-Type> header.
If the optional I<noparams> is true,
suppress the "B<;I<param>=I<value>>" suffices.


=cut

sub ContentType($;$)
{ my($this,$noParams)=@_;
  $noParams=0 if ! defined $noParams;

  my($cte)=$this->Type().'/'.$this->SubType();
  if (! $noParams)
  { my $params = $this->_TypeParams();
    for my $param (sort keys %$params)
    { my $value=$params->{$param};
      $param=lc($param);
      $param =~ s/_/-/g;

      $cte.="; $param=\"$value\"";
    }
  }

  $cte;
}

sub WriteItem($$)
{ my($this,$sink)=@_;

  my $body = $this->Body();

  my $n = $this->SUPER::WriteItem($sink, $body);

  if (! defined $n)
  { my@c=caller;
    warn "SUPER::WriteItem fails from [@c]";
    return undef;
  }

  return $n;
}

sub WriteParts($$$;$$$)	# (this,sink,\@cs::MIME,pre,post,sep)
{ my($this,$sink,$mlist,$pre,$post,$sep)=@_;
  $pre='' if ! defined $pre;
  $post='' if ! defined $post;
  $sep=$this->_TypeParams()->{BOUNDARY} if ! defined $sep;

  $this->Del(CONTENT_LENGTH);

  my($type,$subtype)=($this->Type(), $this->SubType());
  if ($type ne MULTIPART)
  {
    if (@$mlist > 1)
    { my @c = caller;
      warn "$::cmd: type=\"$type\" but more than one item\n\tfrom[@c]\n\t";
    }

    # if ($type eq TEXT && $subtype eq PLAIN)
    # { $this->Del(CONTENT_TYPE);
  }

  if (! length $sep)
  { ::need(cs::Date);
    $sep="cs::MIME::".cs::Date::timecode(time,0)."::GMT"
  }

  $this->SUPER::WriteItem($sink,$pre);

  for my $M (@$mlist)
  {
    $sink->Put("\r\n--$sep\r\n");

    $M->Del(CONTENT_LENGTH);	# just in case
    $M->WriteItem($sink);

    $sink->Put($M->Body());
  }

  $sink->Put("\r\n--$sep--\r\n");
  $sink->Put($post);
}

=item Parts(I<wantPrePost>)

Collect pretext, parts and posttext
from the object
(presumably multipart)
and return an array of B<cs::MIME> objects for each part.
If the optional parameter I<wantPrePost> is true,
prefix the pretext and posttext to the array.

=cut

sub Parts($;$)
{ my($this,$wantPrePost)=@_;
  $wantPrePost=0 if ! defined $wantPrePost;

  ###### input stream
  my($s)=$this->BodySource();

  ###### results
  my $pre = '';
  my $post = '';
  my @parts;

  my($boundary)='--'.$this->_TypeParams()->{BOUNDARY};
  my($terminate)=$boundary.'--';

  ## warn "bound=[$boundary]";
  ## warn "M=".cs::Hier::h2a($this,1);

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

  my @mparts = ();

  for my $part (@parts)
  { push(@mparts,
	 new cs::MIME (new cs::Source (SCALAR, \$part)));
  }

  return @mparts if ! $wantPrePost;
  return ($pre,$post,@mparts);
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
