#!/usr/bin/perl
#
# This is a front end for a Sink.
#	- Cameron Simpson <cs@zip.com.au> 23may1996
#
# new cs::Encode Sink, \&encoder[, args-to-encoder]
#   The encoder routine takes a data string and the args-to-encoder
#   and returns encoded data. It returns undef if there isn't enough
#   data to encode (in which case it is expected to save the unprocessed
#   data in some state area probably referenced by the args-to-encoder
#   for later processing). It is called with undef as data at the end
#   as a sentinel to flush any such unprocessed data.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Sink;

package cs::Encode;

@cs::Encode::ISA=qw(cs::Sink);

sub new
{ my($class,$s,$encoder)=(shift,shift,shift);
  my($this);
  $this=new cs::Sink Sink, $s;
  $this->{cs::Encode::ENCODE}=$encoder;
  $this->{cs::Encode::ENCODE_ARGS}=[ @_ ];

  bless $this, $class;
}

sub DESTROY
{ my($this)=shift;

  # print STDERR "DESTROY(",Hier::h2a($this),")\n";

  $this->Write(undef);
  $this->SUPER::DESTROY();
}

sub Write
{ my($this,$data)=@_;
  my($edata);

  $edata=&{$this->{cs::Encode::ENCODE}}($data,
					@{$this->{cs::Encode::ENCODE_ARGS}});

  return undef if ! defined $edata;

  # warn "Encode::Write: this=",Hier::h2a($this),"\n";
  # warn "\tPut($edata)\n";

  $this->{DS}->Put($edata);

  length $data;
}

1;
