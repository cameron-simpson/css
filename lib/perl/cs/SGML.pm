#!/usr/bin/perl
#
# Parser for SGML.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#
# newtok(Input[,State]) -> new cs::Tokenise
# Tok -> new token or undef
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Tokenise;

package cs::SGML;

$::SGDB=1 if exists $ENV{SGDB};

@cs::SGML::ISA=(cs::Tokenise);

undef %cs::SGML::_SpecialCh;
undef %cs::SGML::_SpecialCode;
$cs::SGML::_CodePtn='';
@cs::SGML::_ChList=();
$cs::SGML::_ChRange='';

{ my(@specials)=(	'amp',	'&',	# & must be first
			'lt',	'<',
			'gt',	'>'
		   );
  my($code,$ch);

  while (defined($code=shift @specials)
      && defined($ch  =shift @specials))
  { $cs::SGML::_SpecialCh{$code}=$ch;
    $cs::SGML::_SpecialCode{$ch}=$code;
    push(@cs::SGML::_ChList,$ch);
    $cs::SGML::_ChRange.=$ch;
    $cs::SGML::_CodePtn.='|'.$code;
  }

  $cs::SGML::_CodePtn =~ s/^\|//;
}

sub new
{ my($class,$s)=@_;

  my($this)=(new cs::Tokenise $s, \&match);

  bless $this, $class;
}

sub tok2a
{ my($t)=@_;
  return $t if ! ref $t;
  return "</$t->{TAG}>" if ! $t->{START};
  
  if ($t->{TAG} eq '!--')
  { return "<!--$t->{COMMENTRY}-->";
  }

  my $markup = "<$t->{TAG}";

  for my $attr (sort keys %{$t->{ATTRS}})
  { $markup.=" $attr=\"";
    my $value = $t->{ATTRS}->{$attr};
    $value =~ s/"/\%22/g;
    $markup.="$value\"";
  }

  "$markup>";
}

sub match	# (Data,State) -> (token,tail) or undef
{ local($_)=shift;
  my($State)=shift;
  my($tok,$tail);

  ## /^[^\n]*/; warn "_=[$&]";

  # character entities
  if (/^(\&(\#\d+|[a-z]\w*));?/i)
  { $tok="$1;"; $tail=$';
  }
  # busted char entities
  elsif (/^\&([^#a-z])/i)
  { $tok='&amp;';
    $tail=$1.$';
  }
  # whitespace
  elsif (/^[ \t\n\r]+/)
  { $tok=$&; $tail=$';
  }
  # a word
  elsif (/^([^\s\r<\&]+)([\s\r<\&])/)
  { $tok=$1; $tail=$2.$';
  }
  # comments
  elsif (/^<!--/)
  { $tail=$';
    if ($tail !~ /--!?>/)	# "!?" from brainfucked RealPay coders
    { warn "\"<--\" with no close yet at:\n[$tail]";
      return undef;
    }

    $tok={ TAG => '!--',
	   START => 1,
	   ATTRS => {},
	   COMMENTRY => $`,
	 };
    $tail=$';
  }
  # complete tag?
  elsif (matchTag($_,\$tok,\$tail))
  { $tail =~ /^[^\n]*/;
    ## warn "after tag, tail=[$&]";
  }
  # catch bad syntax - cameron 25jun99
  elsif (m|^<([ \t\n\r]*(/[ \t\n\r]*)?[^-!:\w])|)
  { $tok="&lt;"; $tail=$1.$';
  }
  else
  # no match
  { ## my($it)=$_;
    ## if ($it =~ /\s*[\n\r]/) { $it=$`; }
    ## warn "no match at _=[$_]\n";
    return undef;
  }

  ## warn "SGML::match: [".cs::Hier::h2a($tok,0)."]\n";

  ($tok,$tail);
}

sub matchTag
{ local($_)=shift;
  my($ptok,$ptail)=@_;

  return 0 unless m|^<[ \t\n\r]*(/[ \t\n\r]*)?([-!:?\w]+)[ \t\n\r]*|;

  ## warn "matched [$&]";
  my($tag)=$2;
  my($endtag)=length($1);
  ## debugging DL, probably from nsbmparse
  ## warn "tag=[$tag] endmarker=[$1] endtag=$endtag" if $tag eq DL;

  $_=$';

  my($T)={ TAG => uc($tag),
	   START => $endtag ? 0 : 1,
	   ATTRS => {},
	 };
  warn "blech! (endtag=$endtag)" if ! length($T->{START});

  my($A)=$T->{ATTRS};

  my($at,$val);

  ATTR:
  while (1)
  {
    #     tag                    =           "quoted"          'quoted'         unquoted  
    if (/^([-:\/!\w_]+)([ \t\r\n]*=[ \t\r\n]*("(""|[^"\r\n])*"|'(''|[^'\r\n])*'|[^"'\s>][^>\s]*))?[ \t\n\r]*/)
    {
      $at=$1; $val=(length($2) ? $3 : '');
      $_=$';

      ## warn "at=[$at], val=[$val]\n";
      if (length $val > 1)
      { if ($val =~ /^"/ && $val =~ /"$/
	 || $val =~ /^'/ && $val =~ /'$/
	   )
	{ $val=substr($val,1,length($val)-2);
	}
      }

      $A->{uc($at)}=$val;
    }
    #        "str"
    elsif (/^"([^"\r\n]*)"[ \t\n\r]*/)
    { $A->{$1}=undef;
      $_=$';
      ## warn "$tag: \"$1\"\n";
    }
    # ugly hacks to catch syntax errors and recover
    elsif (/^">/)
    { $_='>'.$';
      ## warn "\"> found, \$_ is now [$_]";
    }
    #        =           "str"   'str'   str
    elsif (/^=[ \t\r\n]*("[^"]*"|'[^']*'|[^[ \t\n\r>]*)[ \t\n\r]*/)
    {
      $_=$';
      ## warn "=foo found";
    }
    #        "    foo=
    elsif (/^"\s+(\w+=)/i)
    # ugly hack
    {
      $_=$1.$';
      warn "lone quote found";
    }
    elsif (/^"[ \t\r\n]*>/)
    {
      $_='>'.$';
    }
    elsif (/^("[^"]*")[ \t\r\n]*/)
    {
      ## warn "skip [$1]";
      $_=$';
    }
    elsif (/^([.,]+)[ \t\r\n]*/)
    {
      ## warn "skip [$1]";
      $_=$';
    }
    elsif (/^([^-<>\s":\/!\w_]+)[ \t\r\n]*/)
    {
      ## warn "skip [$1]";
      $_=$';
    }
    else
    { last ATTR;
    }
  }

  if (/^>/)
  { $$ptok=$T;
    $$ptail=$';
  }
  elsif (/^</)
  { $$ptok=$T;
    $$ptail=$_;
  }
  else
  { 
    (0 && $::SGDB) && warn "fail tag match at [$_]\n";
    return 0;
  }

  $::SGDB && warn "matched ".cs::Hier::h2a($T,0)."\n";

  1;
}

sub unamp
{ local($_)=shift;

  s/^&//;
  s/;$//;
  tr/A-Z/a-z/;

  if (! defined($cs::SGML::_SpecialCh{$_}))
	{ warn "$'cmd: &$_; is not a known special character\n";
	  return "&$_;";
	}

  $cs::SGML::_SpecialCh{$_};
}

1;
