#!/usr/bin/perl
#
# An object composed of a cs::Sink and cs::Source which has calls for each.
# Will take file handles in place of Sinks or Sources.
#	- Cameron Simpson <cs@zip.com.au> 11dec96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Sink;
use cs::Source;
use cs::Hier;

package cs::Port;

sub new
{ my($class,$source,$sink)=@_;

  if (! defined $sink)
  { die "$::cmd: no sink, source not a file handle ("
       .cs::Hier::h2a($source)
       .")"
    if ref $source;

    $sink=_portHandle();
    if (! open($sink,">&".fileno($source)))
    { warn "$::cmd: can't dup $source to $sink: $!";
      return undef;
    }
  }

  if (! ref $source)
  { # warn "source=[$source]";
    $source=new cs::Source (FILE, $source);
    return undef if ! defined $source;
    $source->{FLAGS}&=~$cs::IO::F_NOCLOSE;
  }

  if (! ref $sink)
  { $sink=new cs::Sink FILE, $sink;
    return undef if ! defined $sink;
    $sink->{FLAGS}&=~$cs::IO::F_NOCLOSE;
  }

  bless { IN => $source,
	  OUT => $sink,
	}, $class;
}

$cs::Port::_Handle='Handle0000';
sub _portHandle
{ "cs::Port::".$cs::Port::_Handle++;
}

# stubs to treat a Port object as a Source or Sink
sub Source	{ shift->{IN}; }
sub SourceHandle{ shift->{IN}->Handle(); }
sub Sink	{ shift->{OUT}; }
sub SinkHandle	{ shift->{OUT}->Handle(); }
sub Get		{ my($this)=shift; $this->{IN}->Get(@_); }
sub GetLine	{ my($this)=shift; $this->{IN}->GetLine(@_); }
sub GetContLine	{ my($this)=shift; $this->{IN}->GetContLine(@_); }
sub Put		{ my($this)=shift; $this->{OUT}->Put(@_); }
sub PutFlush	{ my($this)=shift; $this->{OUT}->Put(@_);
				   $this->{OUT}->Flush(); }
sub Flush	{ my($this)=shift; $this->{OUT}->Flush(@_); }
sub Read	{ my($this)=shift; $this->{IN}->Read(@_); }
sub NRead	{ my($this)=shift; $this->{IN}->NRead(@_); }

1;
