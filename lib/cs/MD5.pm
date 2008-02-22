#!/usr/bin/perl
#
# Compute an MD5 hash of something.
#	- Cameron Simpson <cs@zip.com.au> 28aug96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

require 'open2.pl';
require 'flush.pl';

use cs::IO;
use cs::PipeDecode;

package cs::MD5;

$cs::MD5::_MD5_PATH='md5';	# executable, was /opt/bin/md5
undef $cs::MD5::_MD5proc;

sub new
{ my($class)=shift;
  my($to,$from)=(cs::IO::mkHandle(1),cs::IO::mkHandle(1));
  my($pid)=main::open2($from,$to,"exec $cs::MD5::_MD5_PATH -u -");

  return undef if ! defined $pid;

  bless { TO => $to, FROM => $from }, $class;
}

sub Digest
{ my($this,$filename)=@_;
  my($to,$from)=($this->{TO},$this->{FROM});
  print $to $filename, "\n";
  &'flush($to);
  local($_);

  return undef if ! defined ($_=<$from>);
  chomp;

  /^([\da-f]{32})\s/ && return $1;
  undef;
}

sub md5file
{ my($filename)=shift;
  $cs::MD5::_MD5proc=(new cs::MD5) if ! defined $cs::MD5::_MD5proc;
  return undef if ! defined $cs::MD5::_MD5proc;
  $cs::MD5::_MD5proc->Digest($filename);
}

sub md5string
{ my($s)=new cs::PipeDecode (sub { ### warn "exec($cs::MD5::_MD5_PATH)";
				   exec($cs::MD5::_MD5_PATH);
				   ### warn "/*NOTREACHED*/";
				 }, [],
			     ARRAY,[ shift ]);
  return undef if ! defined $s;
  local($_)=$s->GetLine();
  /^([\da-f]{32})\s/ && return $1;
  return undef;
}

1;
