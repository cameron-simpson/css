#!/usr/bin/perl
#
# Parser for HTML.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#
# Recoded to present HTML and SGML as structures. - cameron, 26may95
# Error recovery.				  - cameron, 11may97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::SGML;
use cs::Tokenise;

package cs::HTML;

require Exporter;
@cs::HTML::ISA=qw(Exporter);
@cs::HTML::EXPORT_OK=qw(TABLE TD TR IMG HREF);

%cs::HTML::NoIndent=(	TABLE	=> 1,
			A	=> 1,
			PRE	=> 1,
		    );

$cs::HTML::ImStat=1;

# these expect no closing tag
map($cs::HTML::_Singular{uc($_)}=1,
	BASE,META,INCLUDE,OPTION,INPUT,ISINDEX,INC,P,IMG,HR,BR,'!--');

sub singular
	{ my($tag)=@_;
	  $tag=uc($tag);
	  $tag =~ /^!/ || (exists $cs::HTML::_Singular{$tag}
			&& $cs::HTML::_Singular{$tag});
	}

# these define what nests in what
%cs::HTML::_Structures
=(	DL,	[DT,DD],
	TABLE,	[TR],
	TR,	[TD,TH],
	UL,	[LI],
	OL,	[LI],
 );

# invert the table
for my $enclose (keys %cs::HTML::_Structures)
{ for my $child (@{$cs::HTML::_Structures{$enclose}})
  { if (! exists $cs::HTML::_ParentTags{$child})
    { $cs::HTML::_ParentTags{$child}=[ $enclose ];
    }
    else
    { push(@{$cs::HTML::_ParentTags{$child}},$enclose);
    }
  }
}

# If the parent if a tag is active, we must back out until
# that tag is at the top of the parse stack before accepting it.
# 
sub MustClose
{ my($this,$topTag,$nextTag)=@_;

  my(@ptags)=(exists $cs::HTML::_ParentTags{$nextTag}
	    ? @{$cs::HTML::_ParentTags{$nextTag}}
	    : ()
	     );
  
  # are we at the top already?
  for my $ptag (@ptags)
  { return 0 if $topTag eq $ptag;	# good tag at the top
  }

  my($active)=$this->{ACTIVE};

  # is there an outer tag to pop back to?
  for my $ptag (@ptags)
  { return 1 if $active->{$ptag};
  }

  return 0;
}

# new cs::HTML <cs::Source>
sub new
{ my($class,$s)=(shift,shift);
  if (! ref $s)
  { ::need(cs::Source);
    $s=new cs::Source ($s,@_);
  }

  my($sg)=new cs::SGML $s;

  return undef if ! defined $sg;

  bless { SGML		=> $sg,
	  TOKENS	=> [],
	  ACTIVE	=> {},
	}, $class;
}

sub Tok
{ ## warn "HTML::Tok()\n";
  my($this,$close,$pertok)=@_;
  $close={} if ! defined $close;

  my($t);

  if (! defined ($t=$this->{SGML}->Tok()))
  { ## warn "EOF from SGML, returning\n";
    return undef;
  }

  ## warn "parse TAG=$t->{TAG}\n";
  # scalar or simple tag? return it
  if (! ref $t || singular($t->{TAG}) || ! $t->{START})
  { ## warn "single token ".cs::Hier::h2a($t,0) if ref($t) && $t->{TAG} eq DL;
    $t=&$pertok($t) if defined $pertok;
    return $t;
  }

  my($oldActiveState)=$this->{ACTIVE}->{$t->{TAG}};
  $this->{ACTIVE}->{$t->{TAG}}=1;

  # nesting tag - collect contents

  $t->{TOKENS}=[];

  my($rt);	# raw token

  # update set of closing tokens
  my($enclose)={ %$close };
  $enclose->{$t->{TAG}}=1;

  # collect SGML tokens, assembling structure
  TOK:
    while (defined ($rt=($this->{SGML}->Tok())))
    { ## warn "got SGML tok $rt->{TAG}\n";

      ## warn "$rt\n" if ! ref $rt;

      if (! ref $rt || singular($rt->{TAG}))
      # scalar or singular? keep
      {
	## warn "pushing ".cs::Hier::h2a($rt,0) if $rt->{TAG} eq DL;
	push(@{$t->{TOKENS}},$rt);
      }
      elsif (! $rt->{START})
      # closing tag
      { if (exists $enclose->{$rt->{TAG}})
	# closing tag for something other than this?
	# push back, fall out
	# this way we can parse <tag><tag2></tag1>
	{ $this->{SGML}->UnTok($rt) if $rt->{TAG} ne $t->{TAG};
	  last TOK;
	}
	else
	# unexpected closing token? keep it
	{ ## warn "pushing ".cs::Hier::h2a($rt,0) if $rt->{TAG} eq DL;
	  push(@{$t->{TOKENS}},$rt);
	}
      }
      elsif ($this->MustClose($t->{TAG},$rt->{TAG}))
      # oooh! a self terminating tag
      # back out until we hit the closing parent
      { $this->{SGML}->UnTok($rt);
	## warn "faking /$t->{TAG} to precede $rt->{TAG}\n";
	last TOK;
      }
      else
      # substructure
      { $this->{SGML}->UnTok($rt);
	## warn "recurse into $rt->{TAG} from $t->{TAG}\n";
	push(@{$t->{TOKENS}},$this->Tok($enclose,$pertok));
	## warn "back to $t->{TAG}\n";
      }
    }

  ## warn "RETURN TOKEN $t->{TAG}\n";
  $this->{ACTIVE}->{$t->{TAG}}=$oldActiveState;

  $t=&$pertok($t) if defined $pertok;

  return $t;
}

# make a token
# return token structure in scalar context
# or array of text strings in array context
sub mkTok
{ my($tag,@subtoks)=@_;
  my($attrs)=(@subtoks
	   && ref $subtoks[0]
	   && ::reftype($subtoks[0]) eq HASH
		? shift(@subtoks)
		: {});

  my($tok)={ TAG	=> $tag,
	     ATTRS	=> $attrs,
	     TOKENS	=> [ @subtoks ],
	   };

  wantarray ? tok2a($tok) : $tok;
}

sub tokUnfold
{ my(@tok)=();

  my($tok);

  for (@_)
  { $tok=$_;
    $tok=(ref && ::reftype($_) eq ARRAY ? mkTok(@$_) : $_);

    $tok->{TOKENS}=[ tokUnfold(@{$tok->{TOKENS}}) ]
	  if ref($tok);

    push(@tok,$tok);
  }

  @tok;
}

# get plain text of tok list - discard markup
sub tokFlat
{ my($text)='';

  for my $t (@_)
  {
    if (! ref $t)
    { $text.=$t;
    }
    elsif (::reftype($t) eq ARRAY)
    { $text.=tokFlat(@$t);
    }
    else
    { $text.=tokFlat(@{$t->{TOKENS}});
    }
  }

  $text;
}

sub tok2a
{ my(@html)=();

  my $indent=0;
  if (@_ && $_[0] =~ /^[01]$/)
  { $indent=shift(@_);
  }
  ::need(cs::Sink);
  my($sink)=cs::Sink->new(ARRAY,\@html);
  tok2s($indent,$sink,@_);

  wantarray ? @html : join('',@html);
}

# convert tokens to array of text strings
sub tok2s	# ([indent,]sink,tok...)
{
  my($sink)=shift;
  my $indent=0;
  if (! ref $sink)
	{ $indent=$sink; $sink=shift; }

  my(@html)=();

  ## warn "tok2a:\n".cs::Hier::h2a([@_],1);

  if (@_ < 1)
	{}
  elsif (@_ > 1)
	{ map(tok2s($indent,$sink,$_),@_);
	}
  else
  {
    my($html)=tokUnfold(@_);

    if (! defined $html)
	{ my(@c)=caller;
	  warn "tokUnfold(@_) gives undef from [@c]";
	}
    elsif (! ref $html)
	{ if ($html =~ /^\&(\#\d+|\w+);/) { $sink->Put($html); }
	  else				  { $sink->Put(raw2html($html)); }
	}
    else
    # unfold into stream of tokens
    {
      my($tag)=uc($html->{TAG});

      # work around netscape's busted whitespace => padding bug
      # another with anchors?
      my $indent = doesTagIndent($tag,$indent);

      # empty tags used for simple nesting without markup
      if ($html->{TAG} eq '')
      { tok2s($indent,$sink,@{$html->{TOKENS}});
      }
      elsif ($html->{TAG} =~ /^&/)
      { my($char)=$html->{TAG};
	if (! $char =~ /;$/)
	      { warn "no closing \";\" for \"$char\"";
		$char.=';';
	      }

	$sink->Put($char);
      }
      elsif ($html->{TAG} =~ /^[^\w!]/)
      # weird literal
      { $sink->Put($html->{TAG});
      }
      # include other file
      elsif ($html->{TAG} eq INCLUDE)
      { my($src)=$html->{ATTRS}->{SRC};
	my($is);

	## warn "INCLUDE($src)";
	if (defined ($is=_openSrc($src)))
	      {
		local($_);

		while (defined ($_=$is->Read()) && length)
		      { $sink->Put($_);
		      }

		undef $is;
	      }
	else
	{ tok2s($indent,$sink,
		      [A, {HREF => $src},
			  ( @{$html->{TOKENS}}
				  ? @{$html->{TOKENS}}
				  : "<TT>$src</TT>"
			  )]);
	}
      }
      elsif ($tag =~ /^H(\d+)$/ && $1 > 6)
      # catch bogus Hn tags above the limit
      { tok2s($indent,$sink,
		    [P],"\n",
		    [B,$html->{ATTRS},@{$html->{TOKENS}}],
		    "\n");
      }
      else
      {
	my $subindent=$indent;
	if ($indent > 0)
	{ if (! ref $sink)
	  { my @c = caller; warn "Put from [@c]";
	  }
	  $sink->Put("\n".(" " x ($indent-1)));
	  $subindent=$indent+2;
	}

	$sink->Put(_justtok2a($html));
	for (@{$html->{TOKENS}})
	{ if (! defined)
		{
##				  my(@c)=caller;
##				  warn "undef in TOKENS (html="
##				      .cs::Hier::h2a($html,0)
##				      .") from [@c]";
		}
	  else
	  { tok2s($subindent,$sink,$_);
	  }
	}

	if (! singular($html->{TAG}))
		{ if ($indent > 0)
			{ $sink->Put("\n".(" " x ($indent-1)));
			}

		  $sink->Put('</'.uc($html->{TAG}).'>');
		}
      }
    }
  }
}

sub doesTagIndent
{ my($tag,$oldindent)=@_;

  if (exists $cs::HTML::NoIndent{$tag}
   && $cs::HTML::NoIndent{$tag})
	{ return 0;
	}

  $oldindent;
}

sub _openSrc
{ my($src)=@_;
  my($s);

  ::need(cs::Source);
  cs::Source->new(PATH,$src);
}

# convert token only, not subtokens
sub _justtok2a
{ my($html)=@_;

  my($mu)='<'.uc($html->{TAG});

  for (sort keys %{$html->{ATTRS}})
  { $mu.=" ".uc($_);
    if (defined $html->{ATTRS}->{$_})
    # XXX - quick quoting hack
    { my($val)=$html->{ATTRS}->{$_};

      $val =~ s/[^ !-~]|"/sprintf("%%%02x",ord($&))/eg;

      $mu.="=\"$val\"";
    }
  }

  $mu.='>';

  $mu;
}

%cs::HTML::_AmpEsc=(
	  AMP,		'&',
	  COLON,	':',
	  COMMA,	',',
	  DOLLAR,	'$',
	  EQUALS,	'=',
	  'GT',		'>',
	  HYPHEN,	'-',
	  LDOTS,	'...',
	  LPAR,		'(',
	  LSQB,		'[',
	  'LT',		'<',
	  NBSP,		' ',
	  PERCNT,	'%',
	  PERIOD,	'.',
	  PLUS,		'+',
	  QUEST,	'?',
	  QUOT,		'"',
	  RPAR,		')',
	  RSQB,		']',
	  SEMI,		';',
	  TILDE,	'~',
	 );

sub unamp
	{ local($_)=uc(shift);

	  if (defined $cs::HTML::_AmpEsc{$_})	{ $_=$cs::HTML::_AmpEsc{$_}; }
	  elsif (/^#(\d+)$/)		{ $_=chr($1); }
	  else				{ }

	  $_;
	}

# pattern to match an RFC822 Message-ID
$cs::HTML::_MsgIDPtn='<[^>@]*@[^>@]*>';

sub raw2html	# rawline -> escaped line
{ local($_)=@_;

  if (! defined)
  { my(@c)=caller;
    warn "raw2html(undef) from [@c]";
  }

  # convert special characters
  s:(_)+([&<>]):$2:g;	# clear underlines from specials
  s:(([&<>]))+\2:$2:g;# clear bold from specials
  s:&:&amp;:g;		# replace with SGML escapes
  s:<:&lt;:g;
  s:>:&gt;:g;

  # recognise bold (accomodating underlined bold!)
  s:((_)*(.)(\3)+)+:<b>$&</b>:g;
  s:(.)(\1)+:$1:g;

  # recognise italics
  s:((_)+.)+:<i>$&</i>:g;
  s:(_)+(.):$2:g;

  $_;
}

sub quoteQueryField
{ local($_)=shift;
  s/[&#=%?]/sprintf("%%%02x",ord($&))/eg;
  s/ /+/g;
  $_;
}

sub href	# (tag,url,$target) -> <A HREF=...>...</A>
{ my($tag,$url,$target)=@_;

  '<A HREF="'.&quote($url).'">'.&raw2html($tag).'</A>';
}


sub news2html
	{ local($_)=@_;
	  my($sofar);

	  while (length)
		{ if (/^$cs::SGML::AnnoPtn/o
		   || /^\&\w+;/)	{ $sofar.=$&; $_=$'; }
		  elsif (/^$cs::HTML::_MsgIDPtn/o)	{ $sofar.=&msgid2html($&); $_=$'; }
		  elsif (m;^\w+\://[^"\s]+;){ $sofar.=&HREF($&,$&); $_=$'; }
		  elsif (/^[^<\w&]+/)	{ $sofar.=$&; $_=$'; }
		  else
		  { $sofar.=substr($_,$[,1);
		    substr($_,$[,1)='';
		  }
		}

	  $sofar;
	}

sub msgid2html
	{ my($id)=shift;
	  my($shortid)=$id;

	  $shortid =~ s/^<(.*)>$/$1/;
	  &href($id,"news:$shortid");
	}

# hack on some markup
sub editMarkUp
{ my($editsub)=shift;

  ## warn "editMU: editsub=$editsub, #\@_=".scalar(@_)."\n";
  for (@_)
  {
    $editsub->($_);
    if (ref)
    {
      if (::reftype($_) eq ARRAY)
      # a bit of markup
      { if (@$_ > 1)
	{ my $type = ::reftype($_->[1]);
	  my $a;
	  if (defined($type) && $type eq HASH)	{ $a=2; }	# skip attrs
	  else					{ $a=1; }

	  for my $i ($a..$#$_)
	  { editMarkUp($editsub,$_->[$i]);
	  }
	}
      }
      elsif (::reftype($_) eq HASH)
      { for my $i (0..$#{$_->{TOKENS}})
	{ editMarkUp($editsub,$_->{TOKENS}->[$i]);
	}
      }
    }
  }
}

# we expect structured markup
# $grep is either subroutine expecting a token as argument,
# or a string naming a tag to match
sub grepMarkUp	# (grep,@html) -> @grepped
{ my($grep)=shift;

  local(@cs::HTML::_grepped);
  local($cs::HTML::_grepTag);
  local($cs::HTML::_grepFunc);

  if (ref $grep)
  { $cs::HTML::_grepFunc=$grep;
    editMarkUp(\&_grepMarkUp,@_);
  }
  else
  { $cs::HTML::_grepTag=uc($grep);
    editMarkUp(\&_grepMarkUpForTag,@_);
  }

  @cs::HTML::_grepped;
}
sub _grepMarkUp($)
{ my($t)=@_;
  push(@cs::HTML::_grepped, $t)
	if &$cs::HTML::_grepFunc($t);
}
sub _grepMarkUpForTag($)
{ my($t)=@_;
  push(@cs::HTML::_grepped, $t)
	if ref $t
	&& ( ::reftype($t) eq ARRAY
	   ? $t->[0]
	   : $t->{TAG}
	   ) eq $cs::HTML::_grepTag;
}

sub nbstr
{ my($text,$keepWide)=@_;
  $keepWide=0 if ! defined $keepWide;

  warn "keepWide unimplemented" if $keepWide;

  my(@t)=grep(length,split(/\s+/,$text));
  my(@tok)=();

  for (@t)
  { push(@tok,['&nbsp;']) if @tok;
    push(@tok,$_);
  }

  @tok;
}

sub URLs	# html -> (url,tag,...)
{ local($_)=@_;
  my(@URLs,$anno,$url,$tag,$noslash,%attrs);

  while (m|$cs::SGML::AnnoPtn|oi)
  { $anno=$&;
    $_=$';
    ($tag,$noslash,%attrs)=&AnnoDecode($anno);
    if (defined($attrs{'href'}))
    { $url=$attrs{'href'};
      if ($tag eq 'a'
       && $noslash
       && /^([^>]*)<\s*\/\s*a\s*>/i)
      { $tag=$1; $_=$'; }
      else
      { $tag=''; }

      push(@URLs,$url,$tag);
    }
  }

  @URLs;
}

# Pseudo-attributes:
#	_imPath	Actual pathname of image.
#
sub IMG	# (attrs[,path])
{ my($attrs,$impath)=@_;
  $impath=$attrs->{SRC} if ! defined $impath;

  if (! defined $attrs->{HEIGHT}
   || ! defined $attrs->{WIDTH})
  { ::need(cs::Image);
    my(@size)=cs::Image::imsize($impath);

    if (@size)
    { if (! length $size[0] || ! length $size[1])
      { warn "size($impath)=[@size]\n";
      }

      $attrs->{WIDTH}=$size[0];
      $attrs->{HEIGHT}=$size[1];
    }
  }

  [IMG,$attrs];
}

sub MailTo
{ my($addr)=shift;
  [A,{HREF => "mailto:$addr"},(@_ ? @_ : $addr)];
}
sub H
{ my($level)=shift;
  ["H$level",@_];
}
sub H1	{ H(1,@_); }
sub H2	{ H(2,@_); }
sub LI	{ [LI,@_]; }

sub bodyColours($)
{ my($type)=@_;

  my($bg,$link,$alink,$vlink,$text);

  if ($type eq LIGHT)
  { $bg=WHITE; $text=BLACK;
    $link=BLUE; $alink=RED; $vlink=PURPLE;
  }
  elsif ($type eq DARK)
  { $bg=BLACK; $text=LIME;
    $link=YELLOW; $alink=RED; $vlink=LIME;
  }
  else
  { die "$0: can't do colours for type=\"$type\"";
  }

  { BGCOLOR => $bg,
    TEXT    => $text,
    LINK    => $link,
    ALINK   => $alink,
    VLINK   => $vlink,
  };
}

sub HTML
{ my($title,$headmarkup,$bodyattrs)=(shift,shift,shift);
  $headmarkup=[] if ! defined $headmarkup;
  $bodyattrs={} if ! defined $bodyattrs;

  [HTML,{},[TITLE,{},$title],
		@$headmarkup,
		[BODY,$bodyattrs,@_]
  ];
}

1;
