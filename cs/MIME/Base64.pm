#!/usr/bin/perl
#
# Process MIME base64 encoded data.
#	- Cameron Simpson <cs@zip.com.au> 25jul1996
#
# Bugs: Needs text-mode to make CR-LF <=> LF. Ugh!
#

use strict qw(vars);

use cs::Upd;
use cs::Decode;
use cs::Encode;
use cs::Sink;
use cs::Source;

package cs::MIME::Base64;

$cs::MIME::Base64::_Pad='=';
$cs::MIME::Base64::_Codes=$cs::MIME::Base64::_Pad
       .'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
       .'abcdefghijklmnopqrstuvwxyz'
       .'0123456789+/'
       ;

sub _n2code { substr($cs::MIME::Base64::_Codes,$[+$_[0]+1,1); }
sub _code2n { index($cs::MIME::Base64::_Codes,$_[0])-$[-1; }

sub encode
{ my($data64)='';
  my($sink64)=new cs::MIME::Base64(Encode,new cs::Sink(SCALAR,\$data64));
  $sink64->Put(@_);
  undef $sink64;
  $data64;
}

sub decode
{ my($data64,$isText)=@_;
  $isText=0 if ! defined $isText;

  (new cs::MIME::Base64
	(Decode,
	 (new cs::Source (SCALAR,\$data64)),
	 $isText)
  )->Get();
}

# return a Decode or Encode object using this as its sub-source
sub new
{ my($class,$type,$s,$isText)=@_;
  $isText=0 if ! defined $isText;

  my($this);

  if ($type eq Decode)
  { $this=new cs::Decode $s, \&_Decode, { ISTEXT => $isText };
  }
  elsif ($type eq Encode)
  { $this=new cs::Encode $s, \&_encode, { BUF => '',
				      ISTEXT => $isText,
				    };
  }
  else
  { die "MIME::Base64::new: unknown type \"$type\"";
  }

  return undef if ! defined $this;

  $this;
}

sub _Decode
{ my($this,$newdata,$state)=@_;
  local($_)=$this->{BUF};

  ## warn "into MIME::Base64::_decode($_,[@_])\n";

  # catch EOF sentinel
  return (undef,$_) if ! defined $newdata;

  my($data)='';
  my($a,$b,$c,$d);	# unpacked values
  my($g4);		# encoded values

  # silently ignore other chars
  s:[^A-Za-z0-9+/=]+::g;

  while (length >= 4)
  { $g4=substr($_,$[,4);
    substr($_,$[,4)='';

    # break up into code values
    ($a,$b,$c,$d)=map(_code2n($_),split(//,$g4));

    ## warn "a=$a, b=$b, c=$c, d=$d\n";

    # append data, accomodating '=' padding
    if ($a < 0)
    {
    }
    else
    { $data.=chr(($a<<2) + (($b&0x30)>>4));

      if ($c >= 0)
      { $data.=chr((($b&0x0f)<<4) + (($c&0x3c)>>2));

	if ($d >= 0)
	{ $data.=chr((($c&0x03)<<6) + $d);
	}
      }
    }

    ## warn "data=[$data]\n";
  }

  ($data,$_);
}

sub _encode
{ my($data,$state)=@_;

  if (defined $data)
  { $state->{BUF}.=$data;
  }

  my($encoded)='';
  my($subencode);

  while (length($state->{BUF}) >= 57)
  { $subencode=substr($state->{BUF},$[,57);
    substr($state->{BUF},$[,57)='';

    $encoded.=_encodeN($subencode)."\r\n";
  }

  if (! defined $data)
  { $encoded.=_encodeN($state->{BUF})."\r\n";
    $state->{BUF}='';
  }

  $encoded;
}

sub _encodeN
{ my($subencode)=@_;
  my($subdata);
  my($encoded)='';

  while (length $subencode)
  { $subdata=substr($subencode,$[,3);
    substr($subencode,$[,3)='';
    $encoded.=_encode3($subdata);
  }

  $encoded;
}

sub _encode3
{ my($data)=shift;

  die "\"$data\" is too long" if length $data > 3;

  return '' if ! length $data;

  ## warn "e3($data)\n";

  my($a,$b,$c)=split(//,$data);

  $a=ord($a);
  $b=(length $b ? ord($b) : undef);
  $c=(length $c ? ord($c) : undef);

  my(@codes);

  $codes[0]=($a&0xfc)>>2;
  $codes[1]=($a&0x03)<<4;

  if (defined $b)
  { $codes[1]|=($b&0xf0)>>4;
    $codes[2]=($b&0x0f)<<2;

    if (defined $c)
    { $codes[2]|=($c&0xc0)>>6;
      $codes[3]=$c&0x3f;
    }
    else
    { $codes[3] = -1;
    }
  }
  else
  { $codes[3] = -1;
    $codes[2] = -1;
  }

  join('',map(_n2code($_),@codes));
}

1;
