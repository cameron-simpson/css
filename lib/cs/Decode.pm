#!/usr/bin/perl
#
# This is a front end for a Source.
#	- Cameron Simpson <cs@zip.com.au> 23may96
#
# new cs::Decode Source, \&decoder[, args-to-decoder]
#   The decoder method takes the current new data and the
#   args-to-decoder state supplied at new() time.
#   The new data will be undef at EOF.
#   The complete waiting data is present in $this->{BUF}.
#   The "new data" is present both as a means of passing the undef
#   sentinel and as an effiency measure to save repeated parsing
#   of bulky incomplete data, should the decoding permit this.
#   The method returns the decoded data and any unparsed portion:
#	($decoded,$unparsed)
#   The decoded data may be empty if there is not enough data to
#   decode yet, and undef at EOF if the encoding has the notion.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Decode;

@cs::Decode::ISA=qw(cs::Source);

sub new
{ my($class,$s,$decoder)=(shift,shift,shift);
  my($this)=new cs::Source Source, $s;

  return undef if ! defined $this;

  $this->{cs::Decode::DECODE}=$decoder;
  $this->{cs::Decode::DECODE_ARGS}=[ @_ ];

  bless $this, $class;

  # we wrap it in another layer to use the superclass's
  # $size handler
  new cs::Source Source, $this;
}

sub DESTROY
{ my($this)=shift;

  # return unparsed data to source
  $this->{DS}->_PushBuf($this->{BUF});

  $this->SUPER::DESTROY();
}

sub _Decode
{ my($this,$newdata)=@_;
  my($decoded);

  $this->{BUF}.=$newdata if defined $newdata;

  ($decoded,$this->{BUF})=
	&{$this->{cs::Decode::DECODE}}($this,
				       $newdata,
				       @{$this->{cs::Decode::DECODE_ARGS}});

  $decoded;
}

# read data from source
sub Read
{ my($this,$size)=@_;
  local($_)='';

  while (! length)
  { $_=$this->{DS}->Read();

    ## warn $_;

    if (! length)
    # EOF - send undef sentinel
    { return $this->_Decode(undef);
    }
    else
    { $_=$this->_Decode($_);
    }

    # catch errors
    return undef if ! defined;
  }

  ## warn "cs::Decode::Read: _=[$_]\n";

  $_;
}

1;
