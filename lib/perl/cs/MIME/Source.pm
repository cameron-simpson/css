#!/usr/bin/perl
#

use strict qw(vars);

BEGIN { use DEBUG; DEBUG::using(__FILE__); }

use cs::RFC822;
use cs::Source;
use cs::SubSource;
use cs::CacheSource;
use cs::MIME::Base64;
use cs::MIME::QuotedPrintable;
use cs::Extractor;

package MIME::Source;

$cs::MIME::Source::_range_tspecials='()<>@,;:\\"\/\[\]?.=';

# Make a new MIME object given a Source.
# Afterwards the Source points at the body, with a decoder pushed on if
# necessary.
sub new
	{ my($class,$s)=@_;
	  my($h)=new cs::RFC822;

	  $h->SourceExtract($s);

	  my($this)={ DS	=> $s,
		      HDRS	=> $h,
		      TYPE	=> TEXT,	# text/plain by default
		      SUBTYPE	=> PLAIN,
		      TYPEPARAMS=> {},
		      CTE	=> '7BIT',	# Content-Transfer-Encoding
		    };

	  local($_);

	  $_=$h->Hdr(CONTENT_TYPE);
	  if (! length)
	  	# old-style messages
	  	{ $_='text/plain; charset=us-ascii';
	  	}

	  # Content-Type
	  my($type,$subtype);
	  ($type,$subtype,$_)=parseContentType($_,$this->{TYPEPARAMS});

	  $this->{TYPE}=$type;
	  $this->{SUBTYPE}=$subtype;

	  my($isText)=($type eq TEXT);

	  # Content-Size - push limit onto stream
	  if (defined ($_=$h->Hdr(CONTENT_SIZE)) && /\d+/)
		{ my($size)=$&+0;
		  $this->{DS}=$s=new cs::SubSource $s, $s->Tell(), $size;
		  return undef if ! defined $s;
		}

	  # Content-Transfer-Encoding
	  if (defined ($_=$h->Hdr(CONTENT_TRANSFER_ENCODING)) && length)
		{ s/^\s+//;
		  s/\s+$//;
		  $this->{CTE}=uc($_);
		}

	  # push decoder
	  my($cte)=$this->{CTE};
	  if ($cte eq '8BIT' || $cte eq '7BIT' || $cte eq 'BINARY')
		# recognised null-encodings
		{ }
	  elsif ($cte eq BASE64)
		{ $this->{DS}=$s=new cs::MIME::Base64 Decode, $s, $isText;
		  return undef if ! defined $s;
		}
	  elsif ($cte eq 'QUOTED-PRINTABLE')
		{ $this->{DS}=$s=new cs::MIME::QuotedPrintable Decode, $s, $isText;
		  return undef if ! defined $s;
		}
	  else
	  { warn "new $class: can't decode Content-Transfer-Encoding \"$cte\"";
	  }

	  bless $this, $class;
	}

# unpack the MIME body, return a cs::Extract object
sub Extract
	{ my($this,$dir)=@_;
	  my($e)=new cs::Extractor $dir;

	  return undef if ! defined $e;

	  my($this)=@_;
	  my($s)=$this->{DS};
	  my(@i);

	  my($boundary)='--'.$this->{TYPEPARAMS}->{BOUNDARY};
	  my($terminate)=$boundary.'--';

	  my($line,$bound,$last,$crlf,$ncrlf);
	  my($cache);
	  local($_);

	  $crlf='';

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
			{ if (defined $cache)
				{ if (defined $crlf)
					{ $cache->Put($crlf);
					}

				  $cache->Put($line);
				}
			}
		  else
		  # boundary line
		  { if (! $last)
			{ $cache=$e->Sink();
			  warn "can't make new Sink" if ! defined $cache;
			}

		    undef $ncrlf;	# otherwise it leaks into the next part
		  }

		  $crlf=$ncrlf;
		}

	  # return extracted files
	  $e;
	}

# take a new cs::MIME::Source and an index and return an array of SubSources
#
# note: due to the processing required, for each returned SubSource $s,
# (new cs::MIME::Source $s) must be done to process its headers
sub Parts	# @Index -> @SubSource
	{ my($this,@i)=@_;
	  my($s)=$this->{DS};
	  my(@s,$sub,$i);

	  for $i (sort { $a->{OFFSET} <=> $b->{OFFSET} } @i)
		{ if (defined ($sub=(new cs::SubSource $s,
						   $i->{OFFSET},
						   $i->{SIZE})))
			{ push(@s,$sub);
			}
		}

	  @s;
	}

sub parseTypeParams	# (params-text,\%params) -> unparsed
	{ local($_,$p)=@_;
	  my($param,$value);

	  s/^\s*(;+\s*)+//;
	  PARAM:
	    while (length)
		{ last PARAM unless /^([-\w]+)\s*=\s*/;

		  $param=$1; $_=$';
		  $param=uc($param);

		  if (/^"((\\.|[^\\"])*)"\s*/)
			{ $value=$1; $_=$';
			  $value =~ s/\\(.)/$1/g;
			}
		  else
		  { /^([^\s$_range_tspecials]*)\s*/;
		    $value=$1; $_=$';
		  }

		  $p->{$param}=$value;

	  	  s/^\s*(;+\s*)+//;
		}

	  $_;
	}

sub parseContentType	# (content-type,\%params) -> (type,subtype,unparsed)
	{ local($_,$p)=@_;
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
