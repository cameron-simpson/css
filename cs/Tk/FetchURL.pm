#!/usr/bin/perl
#
# Fetch an URL from the web proxy.
#	- Cameron Simpson <cs@zip.com.au> 26dec99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Net::TCP;
## use cs::HTTP;
use cs::Sink;
use cs::RFC822;

package cs::Tk::FetchURL;

sub new
{ my($class,$w,$url,$notify,$how,$info)=@_;
  die "$::cmd: $class::new: no notify function!" if ! defined $notify;
  $how=FINAL if ! defined $how;	# versus STEPPED for RESPONSE, HEADER, FINAL

  bless { URL	=> $url,
	  W	=> $w,
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

sub Request
{ my($this,$H,$method,$vers)=@_;
  $vers='1.0' if ! defined $vers;
  $H=new cs::RFC822 if ! defined $H;
  $method=GET if ! defined $method;

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

  my $rqurl = $this->{URL};
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
      ## warn "AFTER RESPONSE LINE\nSOFAR=[$this->{SOFAR}]";
      $this->{STATE}=HEADERS;

      if (/^(\d\d\d)\s+/)
      { my($rcode,$rtext)=($1,$');
	warn "response = $rcode [$rtext]";

	$this->{RCODE}=$rcode;
	$this->{RTEXT}=$rtext;
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
	  ($this->{RHEADERS}=new cs::RFC822)->SourceExtract($s);
	}

	$this->{STATE}=DATA;
	$this->{SINK}=cs::Sink::tmpSink();
	$this->{SINKPATH}=$this->{SINK}->Path();

        $this->{SINK}->Put(@a);
	last PARSE;
      }

      if ($this->{MODE} eq STEPPED)
      { &{$this->{NOTIFY}}($this,HEADERS);
      }
    }
    else
    { die "$::cmd: bad or unexpected state \"$this->{STATE}\"";
    }
  }
}

sub Response($)
{ my($this)=@_;
  return () if ! exists $this->{RCODE};
  return ($this->{RCODE}, $this->{RTEXT});
}

sub ResponseHeaders($)
{ my($this)=@_;
  return undef if ! exists $this->{RHEADERS};
  $this->{RHEADERS};
}

sub ResponseSinkPath($)
{ my($this)=@_;
  return undef if ! exists $this->{SINKPATH};
  $this->{SINKPATH};
}

1;
