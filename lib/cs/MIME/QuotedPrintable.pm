#!/usr/bin/perl
#
# Process MIME Quoted-Printable encoded data.
#	- Cameron Simpson <cs@zip.com.au> 25jul96
#

use strict qw(vars);

use cs::Upd;
use cs::Decode;
use cs::Encode;

package cs::MIME::QuotedPrintable;

sub encode
{ my($dataqp)='';
  my($sinkqp)=new cs::MIME::QuotedPrintable(Encode,new cs::Sink(SCALAR,\$dataqp));
  $sinkqp->Put(@_);
  undef $sinkqp;
  $dataqp;
}

sub decode
{ my($dataqp,$isText)=@_;
  $isText=1 if ! defined $isText;

  (new cs::MIME::QuotedPrintable
	(Decode,
	 (new cs::Source (SCALAR,\$dataqp)),
	 $isText)
  )->Get();
}

# return a Decode or Encode object using this as its sub-source
sub new
{ my($class,$type,$s,$isText)=@_;
  $isText=1 if ! defined $isText;

  my($this);

  # warn "new cs::QP [@_]\n";
  if ($type eq Decode)
  { $this=new cs::Decode $s, \&_Decode, { ISTEXT => $isText };
  }
  elsif ($type eq Encode)
  { $this=new cs::Encode $s, \&_encode, { ENCODED => '',
				      ISTEXT => $isText,
				    };
  }
  else
  { die "MIME::QuotedPrintable::new: unknown type \"$type\"";
  }

  return undef if ! defined $this;

  $this;
}

sub _Decode
{ my($this,$newdata,$state)=@_;
  local($_)=$this->{BUF};

  # cs::Upd::err("into MIME::QuotedPrintable::_decode(@_)\n");

  # catch EOF sentinel
  # return unparsed stuff in the raw
  return ($_,'') if ! defined $newdata;

  ## warn "decode($_)\n";

  my($istext)=$state->{ISTEXT};
  my($data)='';

  DECODE:
  while (1)
  { # print STDERR "_=[$_]\n";
    if (/^[^=]+/)
    # literals - common case
    { $data.=$&;
      $_=$';
    }
    elsif (/^=([\da-f][\da-f])/i)
    # hex encoding
    { $data.=chr hex $1;
      $data =~ s/\r\n/\n/ if $istext;
      $_=$';
    }
    elsif (/^=\r?\n/)
    # soft break
    { $_=$';
    }
    elsif (length >= 3)
    # rip off first character as literal
    { $data.=substr($_,$[,1);
      substr($_,$[,1)='';
    }
    else
    { last DECODE;
    }
  }

  ## warn "return ([$data],[$_])";
  ($data,$_);
}

sub _encode
{ local($_)=shift;
  my($state)=shift;
  my($encoded)=$state->{ENCODED};
  my($llen)=$state->{LINELEN}+0;

  if (! defined)
  # EOF - flush pending code
  #
  {
    # warn "MIME::QP: _encode(UNDEF): flushing\n";
    # Remove trailing whitespace character.
    #
    # XXX - This was complicated by a zealous encoding of
    # all the trailing whitespace, since even a single one
    # might push things past the 76 char limit. But then we need
    # to insert soft breaks etc. To hell with it!
    #
    if ($encoded =~ /[\t ]$/)
    { $encoded=$`.uc(sprintf("=%02x",ord($&)));
    }

    return $encoded;
  }

  # warn "QP: encode($_)\n";

  my($retenc)='';	# full lines to return
  my($qp,$chop);
  my($reset);

  while (length)
  { $reset=0;
    if (/^[\t \041-\074\076-\176]+/)
    # literals - common case
    { $qp=$&; $_=$';

      # fill lines
      while (($chop=75-$llen) <= length($qp))
      { $retenc.=$encoded.substr($qp,$[,$chop)."=\r\n";
	substr($qp,$[,$chop)='';
	$encoded='';
	$llen=0;
      }
    }
    elsif (/^\r?\n/)
    # line breaks
    { $qp="=0D=0A=\r\n"; $_=$';
      $reset=1;
    }
    else	# encode as hex
    { $qp=uc(sprintf("=%02x",ord));
      substr($_,$[,1)='';
    }

    # check for overflow
    if ($llen+length($qp) > 75)
    # add pending code and soft break to returned code
    { $retenc.=$encoded."=\r\n";
      $encoded='';
      $llen=0;
    }

    $encoded.=$qp;
    if ($reset)
    { $llen=0; }
    else
    { $llen+=length $qp; }
  }

  # save pending code
  $state->{ENCODED}=$encoded;
  $state->{LINELEN}=$llen;

  $retenc;
}

1;
