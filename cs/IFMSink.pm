#!/usr/bin/perl
#
# IfModified Sink.
# Accepts on PATH type opens, caches the data, and rewrites the original
# (in place) only if the data differ on close.
# Handy for rebuilding indices when you don't want to change the mtime
# unless the index changes.
#	- Cameron Simpson <cs@zip.com.au> 07may99
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Pathname;
use cs::Sink;
use File::Compare;
use File::Copy;

package cs::IFMSink;

@cs::IFMSink::ISA=qw(cs::Sink);

sub new($$$)
{ my($class,$type,$path)=@_;
  die "type(=$type) ne PATH" if $type ne PATH;

  # short circuit if new file
  return new cs::Sink (PATH,$path)
  if ! -e $path;

  my $tmpfile = cs::Pathname::tmpnam();
  my $realsink = new cs::Sink (PATH,$tmpfile);
  return undef if ! defined $realsink;

  # stash parameters
  $realsink->{_IFMSINK_PATH}=$tmpfile;
  $realsink->{_IFMSINK_OPATH}=$path;

  bless $realsink, $class;
}

sub DESTROY
{ my($this)=@_;

  my $tmpfile = $this->{_IFMSINK_PATH};
  my $path    = $this->{_IFMSINK_OPATH};

  # close file, etc
  $this->_DESTROY();	# Super, anyone?
  undef $this;

  if (! -e $path	# mssing or new
   || File::Compare::compare($path,$tmpfile) != 0)
  # different, copy
  { if (! File::Copy::copy($tmpfile,$path))
    { warn "$::cmd: DESTROY cs::IFMSink($path): File::Copy::copy($tmpfile,$path): $!,\n\tnew version left in $tmpfile\n";
      return;
    }
  }

  unlink($tmpfile)
	|| warn "$::cmd: DESTROY cs::IFMSink($path): unlink($tmpfile): $!\n";
}

1;
