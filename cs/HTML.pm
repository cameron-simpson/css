#!/usr/bin/perl
#
# Parser for HTML.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#
# Recoded to present HTML and SGML as structures. - cameron, 26may95
# Error recovery.				  - cameron, 11may97
#

=head1 NAME

cs::HTML - support for parsing and generating HTML markup

=head1 SYNOPSIS

use cs::HTML;

=head1 DESCRIPTION

This module supplies methods for decomposing HTML text
into a data structure
and also methods for converting a perl-friendly data structure
into HTML text.

A B<cs::HTML> object does not represent a tag
but a token source from which HTML tokens
may be read. See L</OBJECT CREATION> below.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::SGML;
use cs::Tokenise;

package cs::HTML;

require Exporter;
@cs::HTML::ISA=qw(Exporter);
@cs::HTML::EXPORT_OK=qw(TABLE TD TR IMG HREF);

sub _tokenURLs($$$$);

# hack to prevent Netscape 4
# inserting gratuitous whitespace into tables
# and to keep A tags on one line
# and to keep PRE sections pristine
%cs::HTML::NoIndent=(	# TABLE	=> 1,
			# A	=> 1,
			PRE	=> 1,
		    );

$cs::HTML::ImStat=1;

# these expect no closing tag
map($cs::HTML::_Singular{uc($_)}=1,
	BASE,META,INCLUDE,OPTION,INPUT,ISINDEX,INC,P,IMG,HR,BR,'!--');

# these define what nests in what
%cs::HTML::_Structures
=(	DL,	[DT,DD],
	TABLE,	[TR],
	TR,	[TD,TH],
	HTML,	[HEAD,BODY],
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

=head1 GENERAL FUNCTIONS

=over 4

=item singular(I<tag>)

Test whether a tag needs a closing E<lt>/I<tag>E<gt> partner.

=cut

sub singular($)
{ my($tag)=@_;
  $tag=uc($tag);
  $tag =~ /^!/ || (exists $cs::HTML::_Singular{$tag}
		&& $cs::HTML::_Singular{$tag});
}

=item mkTok(I<tag>,I<attrs>,I<subtokens...>)

Compose the arguments into a hashref of the form:

S<{ TAG =E<gt> I<tag>, ATTRS =E<gt> I<attrs>, TOKENS =E<gt> [ I<subtokens...> ] }>

If omitted, I<attrs> defaults to C<{}>.

=cut

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

=item tokUnfold(I<tokens...>)

Take a hand constructed array of HTML tokens,
which may contain a mix of tokens in the form

S<{ TAG =E<gt> I<tag>, ATTRS =E<gt> I<attrs>, TOKENS =E<gt> [ I<subtokens...> ] }>

and the form

S<[ I<tag>, I<attrs>, I<subtokens...> ]>

and return a list of these tokens converted into the first form,
suitable for analysis.

=cut

sub tokUnfold
{
  my(@tok)=();

  my $tok;

  for (@_)
  { $tok=$_;
    $tok=(ref && ::reftype($_) eq ARRAY ? mkTok(@$_) : $_);

    $tok->{TOKENS}=[ tokUnfold(@{$tok->{TOKENS}}) ]
	  if ref($tok);

    push(@tok,$tok);
  }

  @tok;
}

=item tokFlat(I<tokens...>)

Take some HTML tokens and return a string containing
the textual component, with all markup discarded.

=cut

# get plain text of tok list - discard markup
sub tokFlat
{ my($text)='';

  for my $t (@_)
  {
    if (! ref $t)
    { my $str = $t;
      my $noamp;
      $str =~ s/\&(\w+);?/(($noamp=unamp($1)) eq $1 ? $& : $noamp)/eg;
      $text.=$str;
    }
    elsif (::reftype($t) eq ARRAY)
    { $text.=tokFlat(@$t);
    }
    else
    { my $tag = $t->{TAG};

      if ($tag eq 'BR' || $tag eq 'LI')
      { $text.="\n";
      }
      elsif ($tag eq 'P')
      { $text.="\n\n";
      }

      $text.=tokFlat(@{$t->{TOKENS}});
    }
  }

  $text;
}

=item tok2a(I<doindent>,I<tokens...>)

take a list of HTML tokens and return the HTML text,
nicely indented if I<doindent> = 1.
If omitted, I<doindent> defaults to 0.
In a scalar context returns a single string with the HTML in it.
In an array context returns an array of strings, each an HTML token.

=cut

sub tok2a
{
  my(@html)=();

  my $indent=0;
  if (@_ && $_[0] =~ /^[01]$/)
  { $indent=shift(@_);
  }

  { ::need(cs::Sink);
    my $sink = cs::Sink->new(ARRAY,\@html);
    tok2s($indent,$sink,@_);
  }

  wantarray ? @html : join('',@html);
}

=item tok2s(I<doindent>,I<sink>,I<tokens...>)

Take a list of HTML tokens and write the HTML text to I<sink> (a B<cs::Sink>),
nicely indented if I<doindent> = 1.
If omitted, I<doindent> defaults to 0.

=cut

# convert tokens to array of text strings
sub tok2s	# ([indent,]sink,tok...)
{
  my($sink)=shift;

  my $indent=0;
  if (! ref $sink)
  { $indent=$sink; $sink=shift; }

  ##::need(cs::Hier);
  ##warn "tok2s: indent=$indent\n".cs::Hier::h2a([@_],1);

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

	my $justthetag=_justtok2a($html);
	##warn "sink->Put($justthetag)";
	$sink->Put($justthetag);
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

	  ##warn "sink->Put(/uc($html->{TAG}))";
	  $sink->Put('</'.uc($html->{TAG}).'>');
	}
      }
    }
  }
}

=item doesTagIndent(I<tag>,I<currentindent>)

If I<tag> is one of the special ones we don't indent, return 0.
Otherwise, return I<currentindent>.

=cut

sub doesTagIndent($$)
{ my($tag,$oldindent)=@_;

  return 0 if exists $cs::HTML::NoIndent{$tag}
	   && $cs::HTML::NoIndent{$tag};

  $oldindent;
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

=item unamp(I<entity>)

Convert a character entity name I<entity>
(as is found inside B<&>I<entity>B<;> in HTML text)
or number (of the form B<#n>)
into the corresponding character.
Returns the character,
of the entity name unchanged if unrecognised.

=cut

sub unamp($)
{
  local($_)=uc(shift);

  if (defined $cs::HTML::_AmpEsc{$_})	{ $_=$cs::HTML::_AmpEsc{$_}; }
  elsif (/^#(\d+)$/)		{ $_=chr($1); }
  else				{ }

  $_;
}

# pattern to match an RFC822 Message-ID
$cs::HTML::_MsgIDPtn='<[^>@]*@[^>@]*>';

=item raw2html(I<text>)

Convert plaintext to HTML,
converting special characters like E<lt> into character entities.
Also recognised is nroff-style bold and underline
(cB<BS>c and _<BS>c respectively).

=cut

sub raw2html($)	# rawline -> escaped line
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

=item quoteQueryField

Replace saces with B<+>.
Replace special characters with B<%xx> escapes.
Used to massage a string for use with a B<GET> HTML query.

=cut

sub quoteQueryField($)
{ local($_)=shift;
  s/[&#=%?]/sprintf("%%%02x",ord($&))/eg;
  s/ /+/g;
  $_;
}

=item href(I<tagline>,I<url>,I<target>)

B<OBSOLETE>.
Emit HTML text for an S<E<lt>A HREF=> anchor.

=cut

sub href	# (tag,url,$target) -> <A HREF=...>...</A>
{ my($tag,$url,$target)=@_;

  '<A HREF="'.quote($url).'">'.raw2html($tag).'</A>';
}


=item news2html(I<text>)

Convert I<text> into HTML text
in a heuristic fashion,
recognising markup and URLs.
Hoped to be handy for mail/news-E<gt>HTML conversion.

=cut

sub news2html($)
{ local($_)=@_;

  my $sofar = '';

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

=item msgid2html(I<message-id>)

Emit HTML text with a S<E<lt>A HREF=news:I<message-id>> anchor.

=cut

sub msgid2html($)
{ my($id)=shift;

  my($shortid)=$id;

  $shortid =~ s/^<(.*)>$/$1/;
  &href($id,"news:$shortid");
}

=item editMarkUp(I<editsub>,I<tokens...>)

Walk the I<tokens>,
handing each to the subroutine I<editsub> for manipulation.

=cut

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

=item grepMarkUp(I<grep>,I<tokens...>)

Seach the I<tokens>,
returning an array of items matching I<grep>.
I<grep> is either a subroutine expecting a token as argument
or a string naming a tag to match.

=cut

# we expect structured markup
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

=item nbstr(I<string>,I<keepWide>)

Return an array of HTML tokens
with the white space in I<string>
replaced with B<&nbsp;>.

UNIMPLEMENTED:
if the optional flag I<keepWide> is supplied,
uses as many B<&nbsp;>s as spaces in the original text,
otherwise uses just one between words.

=cut

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

=item URLs(I<string>)


OBSOLETE.
Return all the URls referenced by B<HREF=> attributes
from the I<string>.

=cut

sub URLs($)	# html -> (url,tag,...)
{ local($_)=@_;

  my@c=caller;die "cs::HTML::URLs(@_)\n\tfrom [@c]\n\t";

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

sub sourceURLs($$;$$$)
{ my($urls,$s,$inline,$base,$noanchors)=@_;
  $inline=0 if ! defined $inline;
  $noanchors=0 if ! defined $noanchors;

  ## my@c=caller;
  ## warn "s=$s from [@c]";

  my $parse = new cs::HTML $s;

  my $t;

  my @urls = ();

  while (defined ($t=$parse->Tok()))
  { ## warn "got $t->{TAG} from HTML::Tok()";
    ## warn cs::Hier::h2a($t,1), "\n";
    push(@urls,tokenURLs($urls,$t,$inline,$base,$noanchors));
    ## warn "getting another token";
  }

  return @urls;
}

sub tokenURLs($$;$$$)
{ my($urls,$t,$inline,$base,$noanchors)=@_;
  $inline=0 if ! defined $inline;
  $noanchors=0 if ! defined $noanchors;

  local($cs::HTML::_BaseURL)=$base;
  return _tokenURLs($urls,$t,$inline,$noanchors);
}

sub _tokenURLs($$$$)
{ my($urls,$t,$inline,$noanchors)=@_;
  $noanchors=0 if ! defined $noanchors;

  ::need(cs::URL);

  my @urls = ();

  # notice <BASE HREF="...">
  my $tag = $t->{TAG};
  my $A = $t->{ATTRS};

  if ($tag eq BASE)
  { if (exists $A->{HREF} && defined($A->{HREF}) && length($A->{HREF}))
    { $cs::HTML::_BaseURL=$A->{HREF};
    }
  }

  ATTR:
  for my $attr ( $inline ? (SRC,BACKGROUND) : HREF )
  { next ATTR if ! exists $A->{$attr}
	      || ! defined $A->{$attr};

    my $url = $A->{$attr};
    my $NU = cs::URL->new($url,$cs::HTML::_BaseURL);
    if (! defined $NU)
    { ## warn "UNDEF from new cs::URL($url, $cs::HTML::_BaseURL)";
    }
    else
    { $url = $NU->Text();
    }

    $url=cs::URL::undot($url);
    push(@urls,$url);

    ##warn "tokFlat(".cs::Hier::h2a($t->{TOKENS},0).")...";
    my $title=join('',cs::HTML::tokFlat(@{$t->{TOKENS}}));
    $title =~ s/\s+/ /g;
    $title =~ s/^ //;
    $title =~ s/ $//;

    if (! exists $urls->{$url} || length($urls->{$url}) < length($title))
    { $urls->{$url}=$title;
      ##warn "title($url)=[$title]";
    }
  }

  SUBTOKENS:
  for my $tok (@{$t->{TOKENS}})
  { push(@urls,_tokenURLs($urls,$tok,$inline,$noanchors));
  }

  return @urls;
}

sub oldTokenURLs($$;$$)
{ my($urls,$t,$inline,$base)=@_;
  $inline=0 if ! defined $inline;

  local $cs::URL::_GrepInline = $inline;

  URL:
  for (cs::HTML::grepMarkUp(\&_htmlGrepSub, $t))
  { my $attrs = $_->{ATTRS};
    my @tags = _htmlGrepTags($cs::URL::_GrepInline);
    ATTR:
    for my $attr (@tags)
    { next ATTR if ! exists $attrs->{$attr}
		|| ! defined $attrs->{$attr};

      my $url = $attrs->{$attr};

      ::need(cs::URL);
      my $NU = cs::URL->new($url,$base);
      if (! defined $NU)
      { ## warn "UNDEF from new cs::URL($url, $base)";
      }
      else
      { $url = $NU->Text();
      }

      $url=cs::URL::undot($url);
      my $title=join('',cs::HTML::tokFlat(@{$_->{TOKENS}}));
      $title =~ s/\s+/ /g;
      $title =~ s/^ //;
      $title =~ s/ $//;

      if (! exists $urls->{$url} || length($urls->{$url}) < length($title))
      { $urls->{$url}=$title;
      }
    }
  }
}

sub _htmlGrepTags($)
{
  $_[0] ? (SRC,BACKGROUND) : HREF;
}

sub _htmlGrepSub($)
{ my($tok)=@_;

  ## warn "tok=".cs::Hier::h2a($tok)."\n";
  ## warn "\@_=[@_]\n";

  return 0 if ! ref $tok;
  my @attrs = keys %{$tok->{ATTRS}};
  return 0 if ! @attrs;

  my @tags = _htmlGrepTags($cs::URL::_GrepInline);

  ## warn "look for [@tags] in [".join(",",keys %{$tok->{ATTRS}})."]\n";

  for my $tag (@tags)
  { return 1 if grep($_ eq $tag, @attrs);
  }

  0;
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

=back

=head1 OBJECT CREATION

=over 4

=item new cs::HTML I<source>

Attach to the B<cs::Source> object I<source>,
ready to return HTML tokens via the B<Tok> method, below.

=item new cs::HTML I<SourceType>,I<SourceArgs...>

Call S<new B<cs::Source> I<SourceType>,I<SourceArgs...>>
to open a B<cs::Source> object and attach,
ready to return HTML tokens via the B<Tok> method, below.

=cut

# new cs::HTML <cs::Source>
sub new
{ my($class,$s)=(shift,shift);
  if (! ref $s)
  { ::need(cs::Source);
    my@c=caller;
    warn "!ref s: s=$s from [@c]";
    $s=cs::Source->new($s,@_);
  }

  my($sg)=new cs::SGML $s;

  return undef if ! defined $sg;

  bless { SGML		=> $sg,
	  TOKENS	=> [],
	  ACTIVE	=> {},
	  TAGSTACK	=> [],
	}, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Tok(I<close>,I<pertok>)

Fetch the next HTML token from the source.

I<close> is an optional hashref whose keys name tags
which imply a close of an active (``open'') tag
(for example, an opening E<lt>B<TR>E<gt> tag
implicitly closes any active E<lt>B<TD>E<gt> tag).

I<pertok> is an optional subroutine to manipulate a tag
before it is returned from B<Tok>. It takes the token as argument.

B<Tok> returns completed tags,
with nested structure embedded in the B<TOKENS> field
of the returned token.
Per-markup element parsing should use the B<cs::SGML> module,
on which B<cs::HTML> is built.

=cut

sub Tok
{ ## warn "HTML::Tok()\n";
  my($this,$close,$pertok)=@_;
  $close={} if ! defined $close;

  my($t);

  # on EOF, fake up closing tags until stack of open tags exhausted
  if (! defined ($t=$this->{SGML}->Tok()))
  { ## warn "EOF from SGML, returning\n";
    my $ts = $this->{TAGSTACK};
    return undef if ! @$ts;

    # fake up a closing tag
    my $tsv = $ts->[$#$ts];
    $t={ TAG => $tsv->{TAG}, START => 0, };
  }

  # scalar or simple tag? return it
  if (! ref $t || singular($t->{TAG}) || ! $t->{START})
  { ## warn "single token ".cs::Hier::h2a($t,0) if ref($t) && $t->{TAG} eq DL;
    $t=&$pertok($t) if defined $pertok;
    return $t;
  }

  # the evil <script> tag - gobble up the script
  if ($t->{TAG} eq 'SCRIPT')
  { ## warn "found SCRIPT, skipping to /SCRIPT ...";

    my($skip,$match)=$this->{SGML}->SkipToRegexp('<\s*/\s*script\s*>',1);
    if (! defined $skip)
    { $t=&$pertok($t) if defined $pertok;
      return $t;
    }

    ## warn "SCRIPT:\n\tCLOSE=\"$match\"\n\tskip=[$skip]\n\n";

    $t->{TOKENS}=[ $skip ];

    return $t;
  }

  #################
  # opening tag ...

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
    { if (1 ## exists $cs::HTML::_Structures{$rt->{TAG}}
       && exists $enclose->{$rt->{TAG}})
      # closing tag for something other than this?
      # push back, fall out
      # this way we can parse <tag1><tag2></tag1>
      { if ($rt->{TAG} ne $t->{TAG})
	{ $this->{SGML}->UnTok($rt);
	  ##warn "pushing back </$rt->{TAG}> because inside <$t->{TAG}>";
	}
	last TOK;
      }
      else
      # unexpected closing token? keep it
      { ## warn "pushing ".cs::Hier::h2a($rt,0) if $rt->{TAG} eq DL;
	push(@{$t->{TOKENS}},$rt);
      }
    }
    elsif ($this->_MustClose($t->{TAG},$rt->{TAG}))
    # oooh! a self terminating tag
    # back out until we hit the closing parent
    { $this->{SGML}->UnTok($rt);
      ## warn "faking /$t->{TAG} to precede $rt->{TAG}\n";
      last TOK;
    }
    else
    # substructure
    { $this->{SGML}->UnTok($rt);
      push(@{$this->{TAGSTACK}}, $t);
      ## warn "recurse into $rt->{TAG} from $t->{TAG}\n";
      push(@{$t->{TOKENS}},$this->Tok($enclose,$pertok));
      ## warn "back to $t->{TAG}\n";
      pop(@{$this->{TAGSTACK}});
    }
  }

  ## warn "RETURN TOKEN $t->{TAG}\n";
  $this->{ACTIVE}->{$t->{TAG}}=$oldActiveState;

  $t=&$pertok($t) if defined $pertok;

  ## warn "MATCH: ".tok2a($t);
  return $t;
}

# If the parent if a tag is active, we must back out until
# that tag is at the top of the parse stack before accepting it.
# 
sub _MustClose
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

sub _openSrc
{ my($src)=@_;
  my($s);

  ::need(cs::Source);
  cs::Source->new(PATH,$src);
}

=back

=head1 SEE ALSO

cs::HTML::Form(3),
cs::CGI(3),
cs::SGML(3),
cs::Sink(3),
cs::Source(3),
cs::Tokenise(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
