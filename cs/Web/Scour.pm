#!/usr/bin/perl
#
# Do various scourings of web content.
#	- Cameron Simpson <cs@zip.com.au> 03mar99
#

use strict qw(vars);

use IPC::Open2;

package cs::Web::Scour;

# what we usually scour
cs::HTTP::Scour::DfltType='text/html';

sub scourFILE($$$$$)
{ my($FILE)=shift;

  ::need(cs::Source);
  my $s = cs::Source->new(FILE,$FILE);
  scourSource($s,@_);
}

sub scourSource($$$$$$)
{ my($src,$dest,$dropHTTPresponse,$hasHeaders,$refpfx,$baseURL)=@_;

  if ($dropHTTPresponse)
  {
    if (! length $src->GetLine())
    { warn "$::cmd: unexpected EOF looking for leading HTTP response\n";
      return undef;
    }
  }

  my $type = $cs::HTTP::Scour::DfltType;
  if ($hasHeaders)
  {
    HDR:
    while (defined ($_=$src->GetLine()))
    { print unless /^content-length:/i;	# hah!
      if (/^content-type:\s+([^;\s]+)/i)
      { $type=lc($1);
      }
      elsif (/^\r?$/)
      { last HDR;
      }
    }
  }

  if ($type eq 'image/gif')
  { if (open(GIFSICLE,"| exec gifsicle '#0'"))
    { select(GIFSICLE);
    }

    while (length ($_=$src->Read()))
    { $dest->Put($_);
    }
    return 0;
  }

  # short circuit if wrong type
  if (! $::Force && !grep($type eq $_, @::CleanTypes))
  {
    while (length ($_=$src->Read()))
    { print;
    }
    return 0;
  }

  # assume text/html

  my $parse = new cs::SGML $src;
  my $base;

  if (defined $baseURL)
  { $base = new cs::URL $baseURL;
    print cs::SGML::tok2a({ TAG => A,
			    START => 1,
			    ATTRS => { HREF => $baseURL },
			  }), "Unscour $baseURL", "</A><BR><HR>\n";
  }

  { my $t; my $atok;
    while (defined ($t=$parse->Tok()))
    { $t=scour($t,$refpfx,$base,$recurse);
      print cs::SGML::tok2a($t);
    }
  }

  return 0;
}

  if ($dropHTTPresponse)
  { length $s->GetLine()
	  || die "$::cmd: unexpected EOF looking for leading HTTP response\n";
  }

  my $type = $::DfltType;
  if ($hasHeaders)
  {
    HDR:
    while (defined ($_=$s->GetLine()))
    { print unless /^content-length:/i;	# hah!
      if (/^content-type:\s+([^;\s]+)/i)
      { $type=lc($1);
      }
      elsif (/^\r?$/)
      { last HDR;
      }
    }
  }

  ## warn "type=[$type]";

  # short circuit if wrong type
  if (! $::Force && !grep($type eq $_, @::CleanTypes))
  {
    while (length ($_=$s->Read()))
    { print;
    }
    return 0;
  }

  if ($type eq 'image/gif')
  { if (open(GIFSICLE,"| exec gifsicle '#0'"))
    { select(GIFSICLE);
    }

    while (length ($_=$s->Read()))
    { print;
    }
    return 0;
  }

  # assume text/html

  my $parse = new cs::SGML $s;
  my $base;

  if (defined $baseURL)
  { $base = new cs::URL $baseURL;
    print cs::SGML::tok2a({ TAG => A,
			    START => 1,
			    ATTRS => { HREF => $baseURL },
			  }), "Unscour $baseURL", "</A><BR><HR>\n";
  }

  { my $t; my $atok;
    while (defined ($t=$parse->Tok()))
    { $t=scour($t,$refpfx,$base,$recurse);
      print cs::SGML::tok2a($t);
    }
  }

  return 0;
}

use strict qw(vars);

use cs::Misc;
use cs::Source;
use cs::SGML;
use cs::URL;

# default: strip colours and fonts
$cs::HTML::Scour::TagMap={ BGCOLOR => 1,
	   BORDERCOLOR => 1,
	   BACKGROUND => 1,
	   TEXT => 1,
	   LINK => 1,
	   ALINK => 1,
	   VLINK => 1,
	   'LINK/REL' => 1,
	   'FONT/FACE' => 1,
	   'FONT/SIZE' => 1,
	   'FONT/COLOR' => 1,
	   'LAYER/LEFT' => 1,
	   'LAYER/RIGHT' => 1,
	   'LAYER/TOP' => 1,
	   'LAYER/BOTTOM' => 1,
	   'LAYER/WIDTH' => 1,
	   'TABLE/WIDTH' => 1,
	   'TABLE/CELLPADDING' => 1,
	   'TABLE/SPACING' => 1,
	   'TD/WIDTH' => 1,
	   'TD/HEIGHT' => 1,
	   'P/ALIGN' => 1,
	 };

my $badopts = 0;
my $baseURL;
my $dropHTTPresponse = 0;
my $hasHeaders = 0;
my $refpfx = '';
my $recurse = 0;
my $servePort;
getopts('b:fhHp:u:rt:T:') || ($badopts=1);
$baseURL=$::opt_b if defined $::opt_b;
$::Force=1 if defined $::opt_f;
$dropHTTPresponse=1 if defined $::opt_H;
$hasHeaders=1 if defined $::opt_h;
$servePort=$::opt_p if defined $::opt_p;
$refpfx=$::opt_u if defined $::opt_u;
$recurse=1 if defined $::opt_r;
$::DfltType=lc($::opt_t) if defined $::opt_t;
$::CleanTypes=$::opt_T if defined $::opt_T;
@::CleanTypes=map(lc, grep(length, split(/[,\s]+/, $::CleanTypes)));

if (defined $servePort)
{
  if ($servePort !~ /^(\w+):([-.\w]+(:(\w+))?)$/)
  { warn "$::cmd: bad syntax for port:proxy[:proxyport]\n";
    $badopts=1;
  }
  else
  { $::ListenOn=$1;
    
    my $upstream=$2;
    if ($upstream =~ /:/)
    { $::UpstreamPort=$';
      $::UpstreamHost=$`;
    }
    else
    { $::UpstreamPort=8080;
      $::UpstreamHost=$upstream;
    }
  }
}

# grab extra scouring tags
for (@ARGV)
{ if (/^([a-z_]\w*)(=(\d+))?$/)
  { my $tag = $1;

    my $set = 1;
    if (length $2)
    { $set=$3+0;
    }

    ## print "{$tag}=$set\n";
    $cs::HTML::Scour::TagMap->{uc($tag)}=$set;
  }
  else
  { warn "$::cmd: bad argument: $_\n";
    $badopts=1;
  }
}

die $::Usage if $badopts;

$::TagScour={};
for my $pt (grep(m:/:, keys %$cs::HTML::Scour::TagMap))
{ my($tag,$attr)=($pt =~ m:([^/]*)/+(.*):);
  $tag=uc($tag);
  $attr=uc($attr);

  ## print "{$tag}/{$attr}=$cs::HTML::Scour::TagMap->{$pt}\n";
  $::TagScour->{$tag}={} if ! exists $::TagScour->{$tag};
  $::TagScour->{$tag}->{$attr}=$cs::HTML::Scour::TagMap->{$pt};
}

if (! defined $servePort)
{ exit htclean(STDIN,$dropHTTPresponse,$hasHeaders,$refpfx,$baseURL);
}

# run as proxy server
::need(cs::Net::TCP);
my $this = cs::Net::TCP->new($::ListenOn);
die "$::cmd: can't bind to port $::ListenOn: $!" if ! defined $this;
$this->Serve(($cs::Net::TCP::F_FORK|$cs::Net::TCP::F_FORK2),SERVICE);
# NOTREACHED
exit 1;

#sub SERVICE	# (CONN,peer)
#{ local($CONN,$peer)=@_;
#  local($OUT)=select;
#
#  close($OUT) || warn "$cmd: can't close($OUT): $!\n";
#
#  my $outfh = $CONN->SourceHandle();
#  open(STDOUT,">&$outfh")
#	|| die "$cmd: can't attach stdout to $outfh\n";
#
#  exit htclean($CONN->SourceHandle(), 
#}

sub scourTag($$$$)
{ my($t,$refpfx,$base,$recurse)=@_;

  if (ref $t)
  { my $tag = uc($t->{TAG});

    # disable STYLE tags
    if ($tag eq STYLE)
    { $t->{TAG}=NOSTYLE;
    }

    my $A = $t->{ATTRS};
    my @a = keys %$A;

    if ($tag eq BLINK)
    { $t->{TAG}=EM;
    }
    elsif ($tag eq NOBR)
    { $t->{TAG}=BR;
    }
    elsif ($tag eq LAYER)
    { $t->{TAG}=DIV;
    }
    elsif ($tag eq EMBED)
    { $t->{TAG}=A;
      $A->{HREF}=$A->{SRC};
      unshift(@{$t->{TOKENS}}, "[ EMBEDment of $A->{HREF} ]");
    }
      

    for my $a (@a)
    { my $uca = uc($a);
      if (exists $cs::HTML::Scour::TagMap->{$uca} && $cs::HTML::Scour::TagMap->{$uca}
       || exists $::TagScour->{$tag} && exists $::TagScour->{$tag}->{$uca})
      { delete $A->{$a};
	## print "remove $a from $t->{TAG}\n";
      }
    }

    if (defined $base)
    { for my $a (HREF,SRC,ACTION)
      { if (exists $A->{$a})
	{ $A->{$a}=$base->Abs($A->{$a})->Text();
	}
      }
    }

    if (length $refpfx)
    { if ($recurse && exists $A->{HREF}
       && $A->{HREF} =~ /^(http|ftp):\/\//i
	 )
      { $A->{HREF}="$refpfx/$A->{HREF}";
      }

      if ( ($tag eq FRAME || $tag eq IMG)
	&& exists $A->{SRC}
        && $A->{SRC} =~ /^(http|ftp):\/\//i
	 )
      { $A->{SRC}="$refpfx/$A->{SRC}";
      }
    }
  }

  $t;
}

sub scourTag($$$$)
{ my($t,$refpfx,$base,$recurse)=@_;

  if (ref $t)
  { my $tag = uc($t->{TAG});

    # disable STYLE tags
    if ($tag eq STYLE)
    { $t->{TAG}=NOSTYLE;
    }

    my $A = $t->{ATTRS};
    my @a = keys %$A;

    if ($tag eq BLINK)
    { $t->{TAG}=EM;
    }
    elsif ($tag eq NOBR)
    { $t->{TAG}=BR;
    }
    elsif ($tag eq LAYER)
    { $t->{TAG}=DIV;
    }
    elsif ($tag eq EMBED)
    { $t->{TAG}=A;
      $A->{HREF}=$A->{SRC};
      unshift(@{$t->{TOKENS}}, "[ EMBEDment of $A->{HREF} ]");
    }
      

    for my $a (@a)
    { my $uca = uc($a);
      if (exists $cs::HTML::Scour::TagMap->{$uca} && $cs::HTML::Scour::TagMap->{$uca}
       || exists $::TagScour->{$tag} && exists $::TagScour->{$tag}->{$uca})
      { delete $A->{$a};
	## print "remove $a from $t->{TAG}\n";
      }
    }

    if (defined $base)
    { for my $a (HREF,SRC,ACTION)
      { if (exists $A->{$a})
	{ $A->{$a}=$base->Abs($A->{$a})->Text();
	}
      }
    }

    if (length $refpfx)
    { if ($recurse && exists $A->{HREF}
       && $A->{HREF} =~ /^(http|ftp):\/\//i
	 )
      { $A->{HREF}="$refpfx/$A->{HREF}";
      }

      if ( ($tag eq FRAME || $tag eq IMG)
	&& exists $A->{SRC}
        && $A->{SRC} =~ /^(http|ftp):\/\//i
	 )
      { $A->{SRC}="$refpfx/$A->{SRC}";
      }
    }
  }

  $t;
}

1;
