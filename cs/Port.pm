#!/usr/bin/perl
#
# An object composed of a cs::Sink and cs::Source which has calls for each.
# Will take file handles in place of Sinks or Sources.
#	- Cameron Simpson <cs@zip.com.au> 11dec96
#

=head1 NAME

cs::Port - an I/O pairing of a B<cs::Source> and B<cs::Sink>

=head1 SYNOPSIS

use cs::Port;

=head1 DESCRIPTION

A B<cs::Port> is a subclass of B<cs::Port>
tttached to the supplied command.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Sink;
use cs::Source;
use cs::Hier;

package cs::Port;

@cs::Port::ISA=();

=head1 OBJECT CREATION

=over 4

=item new B<cs::Port> (I<source>,I<sink>)

Return a wrapper for the I<source> and I<sink> pair
supplied.
I<source> is normally a B<cs::Source> object
but may be a filehandle.
I<sink> is normally a B<cs::Sink> object
but may be a filehandle.
If I<sink> is omitted
and I<source> is a filehandle
then I<sink> is taken to be the same file handle.

=cut

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

  bless { cs::Port::IN => $source,
	  cs::Port::OUT => $sink,
	}, $class;
}

=back

=head1 OBJECT METHODS

Most of the B<cs::Source> and B<cs::Sink> methods
can be called on a B<cs::Port>
and they will be handled by the source or sink as appropriate.

=over 4

=cut

sub DESTROY{}

$cs::Port::_Handle='Handle0000';
sub _portHandle
{ "cs::Port::".$cs::Port::_Handle++;
}

=item Source()

Return the B<cs::Source>.

=item Sink()

Return the B<cs::Sink>.

=cut

sub Source	{ shift->{cs::Port::IN}; }
sub Sink	{ shift->{cs::Port::OUT}; }

=item SourceHandle()

Return the file handle of the B<cs::Source>, if any.

=item SinkHandle()

Return the file handle of the B<cs::Sink>, if any.

=cut

sub SourceHandle{ shift->Source()->Handle(); }
sub SinkHandle	{ shift->Sink()->Handle(); }

=item Get(), GetLine(), GetContLine(), Read(), NRead()

Called on the B<cs::Source>.

=cut

sub Get		{ my($this)=shift; $this->Source()->Get(@_); }
sub GetLine	{ my($this)=shift; $this->Source()->GetLine(@_); }
sub GetContLine	{ my($this)=shift; $this->Source()->GetContLine(@_); }
sub Read	{ my($this)=shift; $this->Source()->Read(@_); }
sub NRead	{ my($this)=shift; $this->Source()->NRead(@_); }

=item Put(), PutFlush(), Flush()

Called on the B<cs::Sink>.

=cut

sub Put		{ my($this)=shift; $this->Sink()->Put(@_); }
sub PutFlush	{ my($this)=shift; $this->Sink()->Put(@_);
				   $this->Sink()->Flush(); }
sub Flush	{ my($this)=shift; $this->Sink()->Flush(@_); }

=back

=head1 SEE ALSO

cs::Source(3), cs::Sink(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
