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

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::ALL;
use cs::Date;
use cs::RFC822;
use cs::Source;
use cs::SubSource;
use cs::CacheSource;
use cs::MIME::Base64;
use cs::MIME::QuotedPrintable;

package cs::MIME;	# cs::ALL::useAll();

$cs::MIME::_Range_tspecials='()<>@,;:\\"\/\[\]?.=';

# Make a new cs::MIME object given a Source.
# With no Source, just makes a cs::MIME object.
# $usecsize says to trust the Content-Size header if supplied
# (default is not to).
# If you supply a Source, the entire message gets read from the
# Source so it's in a well-defined place (just past the message).
# To avoid this cost, call with no Source and use cs::RFC822 to
# get the headers and stuff them into the cs::MIME object with
# UseHdrs().
#
sub new
{ my($class,$s,$usecsize)=@_;
  $usecsize=0 if ! defined $usecsize;

  my($this)=bless {
	      TYPE	=> TEXT,	# text/plain by default
	      SUBTYPE	=> PLAIN,
	      TYPEPARAMS=> {},
	      CTE	=> '8BIT',	# Content-Transfer-Encoding
	    }, $class;

  my($h)=new cs::RFC822;

  if (defined $s)
  { $s=new cs::Source (PATH, $s) if ! ref $s;
    return undef if ! defined $s;
    $h->SourceExtract($s);
  }

  $this->UseHdrs($h);

  if (defined $s)
  # do stuff with stream
  {
    # Content-Size - push limit onto stream
    if ($usecsize
     && defined ($_=$h->Hdr(CONTENT_SIZE))
     && /\d+/)
    { my($size)=$&+0;
      $s=new cs::SubSource ($s, $s->Tell(), $size);
      return undef if ! defined $s;
    }

    # suck up the stream
    # this way original stream is at a well defined spot
    # (just past the message) regardless of whether the
    # caller asks for the body
    $this->{DS}=new cs::Source (SCALAR,$s->Get());
    return undef if ! defined $this->{DS};

    # Content-Transfer-Encoding
    if (defined $s
     && defined ($_=$h->Hdr(CONTENT_TRANSFER_ENCODING,1))
     && length)
    { s/^\s+//;
      s/\s+$//;
      $this->{CTE}=uc($_);
    }
  }

  bless $this, $class;
}

sub Hdrs	{ shift->{HDRS} }
sub Type	{ shift->{TYPE}; }
sub SubType	{ shift->{SUBTYPE}; }

sub UseHdrs
{ my($this,$h)=@_;

  $this->{HDRS}=$h;

  local($_);

  $_=$h->Hdr(CONTENT_TYPE);
  if (! defined || ! length)
	# old-style messages
	{ $_='text/plain; charset=us-ascii';
	}

  # Content-Type
  my($type,$subtype);
  ($type,$subtype,$_)=parseContentType($_,$this->{TYPEPARAMS});

  $this->{TYPE}=$type;
  $this->{SUBTYPE}=$subtype;
}

sub _RawSource	{ shift->{DS}; }

# decoded Source - use $this->_RawSource() for raw data
sub _Source
	{ my($this)=@_;
	  my($s)=$this->_RawSource();

	  # push decoder
	  my($cte)=$this->{CTE};
	  my($isText)=($this->{TYPE} eq TEXT);

	  decodedSource($s,$cte,$isText);
	}

sub RawGet	{ shift->_RawSource()->Get(); }
sub Get		{ shift->_Source()->Get(); }

sub RawBody	{ my($this)=@_;
		  my($body)=$this->RawGet();
		  $this->{DS}=new cs::Source (SCALAR,$body);
		  $body;
		}
sub Body	{ my($this)=@_;
		  my($rawbody)=$this->RawBody();
		  my($cte,$isText)=($this->{CTE},
				    ($this->{TYPE} eq TEXT)
				   );

		  my $ds = decodedSource((new cs::Source (SCALAR,$rawbody)),
					  $cte,$isText);

		  # unknown encoding?
		  return $rawbody if ! defined $ds;

		  $ds->Get();
		}

sub ContentType	{ my($this,$noParams)=@_;
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

  $sep="cs::MIME::".cs::Date::timecode(time,0)."::GMT"
	if ! length $sep;

  $this->Hdrs()->WriteItem($sink,$pre);

  for my $M (@$mlist)
  {
    $sink->Put("\r\n--$sep\r\n");

    $M->Hdrs()->Del(CONTENT_LENGTH);	# just in case
    $M->Hdrs()->WriteItem();
    while (defined ($_=$M->_RawSource()->Read()) && length)
	  { $sink->Put($_);
	  }
  }

  $sink->Put("\r\n--$sep--\r\n");
  $sink->Put($post);
}

sub decodedSource
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

# collect pretext, pieces, posttext
# return ([scalarSources],pretext,posttext)
sub Pieces
	{ my($this)=@_;

	  ###### input stream
	  my($s)=$this->_Source();

	  ###### results
	  my($slist,$pre,$post)=([],'','');

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
		  if ($line eq $boundary)
			{ $bound=1; $last=0; }
		  elsif ($line eq $terminate)
			{ $bound=1; $last=1; }
		  else	{ $bound=0; }

		  if (! $bound)
			{ if ($last)
				{ $post.=$_;
				}
			  elsif (defined $cache)
				{ $cache.=$_;
				}
			  else	{ $pre.=$_;
				}
			}
		  else
		  # boundary line
		  { 
		    if (defined $cache)
			{ push(@$slist,new cs::Source (SCALAR,$cache));
			}

		    $cache='';
		    undef $ncrlf;	# otherwise it leaks into the next part
		  }

		  $crlf=$ncrlf;
		}

	  ($slist,$pre,$post);
	}

sub parseTypeParams	# (params-text,\%params) -> unparsed
	{ local($_)=shift;
	  my($p)=@_;
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

		  $p->{$param}=$value;

	  	  s/^\s*(;+\s*)+//;
		}

	  $_;
	}

sub parseContentType	# (content-type,\%params) -> (type,subtype,unparsed)
	{ local($_)=shift;
	  my($p)=@_;
	  my($type,$subtype);

	  if (m:^\s*([-\w]+)\s*/\s*([-\w]+)\s*:)
		{ $type=uc($1);
		  $subtype=uc($2);
		  $_=$';

		  $_=parseTypeParams($_,$p);
		}
	  else
	  { $type=TEXT;
	    $subtype=PLAIN;
	  }

	  ($type,$subtype,$_);
	}

1;
