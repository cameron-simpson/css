#!/usr/bin/perl
#
# Fetch an URL from the web proxy.
#	- Cameron Simpson <cs@zip.com.au> 26dec99
#

=head1 NAME

cs::Tk::FetchURL - fetch a URL asynchronously

=head1 SYNOPSIS

use cs::Tk::FetchURL;

=head1 DESCRIPTION

This module implements a module
for dealing with URLs.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Net::TCP;
use cs::Sink;
use cs::Source;

package cs::Tk::FetchURL;

$cs::Tk::FetchURL::_Loaded=1;

=head1 OBJECT CREATION

=over 4

=item new(I<widget>,I<url>,I<notify>,I<how>,I<info>)

Create a new B<cs::Tk::FetchURL> object
to retrieve a URL from the web proxy.

I<widget> is a B<Tk> widget
(we use B<Tk>'s scheduler>.

I<url> is an absolute URL text string
or a B<cs::URL> object.

I<notify> is a subroutine reference to call
when various parts of the HTTP transaction take place.
It is passed two arguments:
this B<cs::Tk::FetchURL> object and a state string,
one of B<RESPONSE>, B<HEADER>, B<FINAL> or B<ABORT>
indicating that the HTTP response,
HTTP header and complete HTTP object are available respectively.
B<ABORT> is passed if the transfer is terminated abnormally
via the B<Abort> method below.

I<how> specifies when I<notify> is called:
B<FINAL> means only when the entire object has been fetched,
B<STEPPED> means to call I<notify> on each of the
B<RESPONSE>, B<HEADER> and B<FINAL> stages.
If omitted, I<how> defaults to B<FINAL>.

I<info> is an optional arbitrary value
for passing any needed state to I<notify>;
it is stored as the B<INFO> field of the object.

=cut

sub new($$$$;$$)
{ my($class,$widget,$url,$notify,$how,$info)=@_;
  $how=FINAL if ! defined $how;	# versus STEPPED for RESPONSE, HEADER, FINAL
  if (! ref $url)
  { ::need(cs::URL);
    $url=cs::URL->new($url);
  }

  bless { URL	=> $url,
	  W	=> $widget,
	  NOTIFY=> $notify,
	  MODE	=> $how,
	  STATE	=> NEW,
	  INFO	=> $info,
	}, $class;
}

sub DESTROY
{ my($this)=@_;
  $this->Abort() if $this->{STATE} ne ABORT && $this->{STATE} ne FINAL;
}

=back

=head1 OBJECT METHODS

=over 4

=item Abort(I<ok>)

Cancel the transfer associated with this object.
I<ok> is an optional happiness flag; it defaults to false.
If false,
I<notify> will be called with the state string B<ABORT>.

=cut

sub Abort($;$)
{ my($this,$ok)=@_;
  $ok=0 if ! defined $ok;

  if (! $ok)
  { $this->{STATE}=ABORT;
  }

  delete $this->{SINK};
  $this->{W}->fileevent($this->{CONN}->SourceHandle(), 'readable', '');
  if (! $ok)
  { &{$this->{NOTIFY}}($this,ABORT);
  }
}

=item SetPathname(I<pathname>)

Specify where to store the data component of the object.
Must be called before the B<Request> method.

=cut

sub SetPathname($$)
{ my($this,$path)=@_;

  if ($this->{STATE} ne NEW)
  { my @c = caller;
    warn "$::cmd: SetPathname($path) called too late, from [@c]";
  }

  $this->{STASHPATH}=$path;
}

=item Request(I<headers>,I<method>,I<version>)

Initiate the fetch from the proxy.
I<headers> is an optional B<cs::RFC822> object
containing headers to accompany the request.
I<method> is an optional specification of the request type,
one of B<GET> or B<POST>.
I<version> is an optional HTTP version level to claim,
defaulting to B<1.0>.

=cut

sub Request($;$$$)
{ my($this,$H,$method,$vers)=@_;
  if (! defined $H)
  { ::need(cs::RFC822);
    $H=cs::RFC822->new();
  }
  $method=GET if ! defined $method;
  $vers='1.0' if ! defined $vers;

  my($proxy,$port)=split(/:/, $ENV{WEBPROXY});
  if ($port !~ /^\d+$/)
  { warn "$::cmd: \$WEBPROXY: bad format: \"$ENV{WEBPROXY}\"\n";
    return undef;
  }

  $this->{RQ_METHOD}=$method;
  $this->{RQ_HEADERS}=$H;
  $this->{RQ_VERSION}=$vers;

  my $C = new cs::Net::TCP ($proxy,$port);
  return undef if ! defined $C;

  my $rqurl = $this->{URL}->Text(1);
  $rqurl =~ s/[ \t\r\n]/sprintf("%%%02x",ord($&))/eg;

  # request stuff
  my $rq = "$method $rqurl HTTP/$vers\n";
  warn "REQUEST IS [$rq]";
  $C->Put($rq);
  $H->WriteItem($C);
  $C->Flush();

  my $w = $this->{W};
  $w->fileevent($C->SourceHandle(),'readable',[ \&_RequestData, $this ]);

  $this->{CONN}=$C;
  $this->{STATE}=INITIAL;
  $this->{SOFAR}='';

  1;
}

# collect data from the fetch
# parse, stash and notify
sub _RequestData
{ my($this)=@_;

  my $C = $this->{CONN};

  my $data = $C->Read();
  ## warn "got [$data] from ".$C->SourceHandle();
  ## warn "got ".length($data)." bytes from ".$C->SourceHandle();
  ## warn "STATE is $this->{STATE}";

  if (! length $data)
  { ## warn "EOF from ".$C->SourceHandle();
    $this->Abort(1);
    $this->{STATE}=FINAL;
    delete $this->{CONN};

    &{$this->{NOTIFY}}($this,FINAL);
    return;
  }

  local($_);

  PARSE:
  while (1)
  { ## warn "LOOP: STATE is $this->{STATE}, SOFAR=[$this->{SOFAR}]";
  
    if ($this->{STATE} eq DATA)
    { $this->{SINK}->Put($data);
      ## warn "<<<<<<<<<<< END PUT";
      last PARSE;
    }

    $this->{SOFAR}.=$data;
    $data='';

    if ($this->{STATE} eq INITIAL)
    # collecting response code
    {
      last PARSE unless $this->{SOFAR} =~ /\n/;

      # complete response line
      $_=$`;
      $this->{SOFAR}=$';
      s/\r$//;

      ## warn "AFTER RESPONSE LINE\n+[=$_]\nSOFAR=[$this->{SOFAR}]";
      $this->{STATE}=HEADERS;

      if (m:^HTTP/\d+\.\d+\s+(\d\d\d)\s+:)
      { my($rcode,$rtext)=($1,$');
	warn "response = $rcode [$rtext]";

	$this->{RCODE}=$rcode;
	$this->{RTEXT}=$rtext;
      }
      else
      { warn "bogus response: [$_]";
      }

      if ($this->{MODE} eq STEPPED)
      { &{$this->{NOTIFY}}($this,RESPONSE);
      }
    }
    elsif ($this->{STATE} eq HEADERS)
    {
      last PARSE unless $this->{SOFAR} =~ /\n\r?\n/;

      # end of headers
      { ## warn "LOOK FOR CONTENT\nSOFAR=[$this->{SOFAR}]";

	my @a = $this->{SOFAR};
        $this->{SOFAR}='';

        { my $s = new cs::Source (ARRAY, \@a);
	  ($this->{RHEADERS}=cs::RFC822->new)->SourceExtract($s);
	}

	if ($this->{MODE} eq STEPPED)
	{ &{$this->{NOTIFY}}($this,HEADERS);
	}

	$this->{STATE}=DATA;
	$this->{SINK}=( exists $this->{STASHPATH}
		      ? new cs::Sink (PATH,$this->{STASHPATH})
		      : cs::Sink::tmpSink()
		      );
	$this->{SINKPATH}=$this->{SINK}->Path();

        $this->{SINK}->Put(@a);
	last PARSE;
      }
    }
    else
    { die "$::cmd: bad or unexpected state \"$this->{STATE}\"";
    }
  }
}

=item Response()

Return an array containing the RCODE and RTEXT of the HTTP
response, or an empty array if the response is not yet available.
Of use to the I<notify> routine.

=cut

sub Response($)
{ my($this)=@_;
  return () if ! exists $this->{RCODE};
  return ($this->{RCODE}, $this->{RTEXT});
}

=item ResponseHeaders()

Return an B<cs::RFC822> object with the response headers,
or B<undef> if the response is not yet available.
Of use to the I<notify> routine.

=cut

sub ResponseHeaders($)
{ my($this)=@_;
  return undef if ! exists $this->{RHEADERS};
  $this->{RHEADERS};
}

=item ResponseSinkPath()

Return the pathname of the temporary file
into which the retrieved object body has been placed.
Of use to the I<notify> routine.

=cut

sub ResponseSinkPath($)
{ my($this)=@_;
  return undef if ! exists $this->{SINKPATH};
  $this->{SINKPATH};
}

=back

=head1 CAVEATS

This is an asynchronous interface.
Unless you return control to B<Tk::MainLoop>
nothing will happen
as the I/O is all done by callbacks.

=head1 ENVIRONMENT

B<WEBPROXY> specified the location of the web proxy
in the form B<I<proxyhost>:I<port>>. I<port> must be numeric.

=head1 SEE ALSO

L<Tk>,
L<cs::URL>,
L<cs::HTTP>,
L<cs::RFC822>

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
