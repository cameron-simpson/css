#!/usr/bin/perl
#
# HTML/SGML-related stuff.	- Cameron Simpson, <cs@zip.com.au>
#

package html;

undef %special_ch, %special_code;
$codeptn='';
@chlist=();
$chrange='';

{ local(@specials)=(	'amp',	'&',	# & must be first
			'lt',	'<',
			'gt',	'>'
		   );
  local($code,$ch);

  while (defined($code=shift @specials)
      && defined($ch  =shift @specials))
	{ $special_ch{$code}=$ch;
	  $special_code{$ch}=$code;
	  push(@chlist,$ch);
	  $chrange.=$ch;
	  $codeptn.='|'.$code;
	}

  $codeptn =~ s/^\|//;
}

# pattern to match =val ==> $1=val
$annoattrvalptn='\s*=\s*([^\s">]+|"[^"]+")';

# pattern to match attr[=val] ==> $1=attr $3=val(if present)
$annoattrptn='\s+(\w+)('.$annoattrvalptn.')?';

# pattern to match attr=val ==> $1=attr $2=val
$annoattrvalptn='\s+(\w+)'.$annoattrvalptn;

# pattern to match an annotation marker ==> $1=type $2=parameters
$annoptn='<\s*(/?\s*\w+)(('.$annoattrptn.')*)\s*>';

# pattern to match an RFC822 Message-ID
$msgidptn='<[^>@]*@[^>@]*>';

sub raw2html	# rawline -> escaped line
	{ local($_)=@_;

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

sub html2raw	# html ==> line without HTML annotation
	{ local($_)=@_;

	  # remode HTML markup
	  s/$annoptn//og;

	  # decode special &code; escapes
	  s/\&($codeptn);?/$special_ch{$1}/og;

	  $_;
	}

sub href	# (tag,url) -> <A HREF=...>...</A>
	{ local($tag,$url)=@_;

	  '<A HREF="'.&quote($url).'">'.&raw2html($tag).'</A>';
	}


sub news2html
	{ local($_)=@_;

	  local($sofar);

	  while (length)
		{ if (/^$annoptn/o
		   || /^\&\w+;/)	{ $sofar.=$&; $_=$'; }
		  elsif (/^$msgidptn/o)	{ $sofar.=&msgid2html($&); $_=$'; }
		  elsif (m;^\w+\://[^"\s]+;){ $sofar.=&href($&,$&); $_=$'; }
		  elsif (/^[^<\w&]+/)	{ $sofar.=$&; $_=$'; }
		  else
		  { $sofar.=substr($_,$[,1);
		    substr($_,$[,1)='';
		  }
		}

	  $sofar;
	}

sub msgid2html
	{ local($id)=shift;
	  local($shortid)=$id;

	  $shortid =~ s/^<(.*)>$/$1/;
	  &href($id,"news:$shortid");
	}

sub quote	# raw -> %xx escaped string
	{ local($_)=shift;
	  local($sofar,$match);

	  $sofar='';
	  LEX:
	    while (/[-\w:\.\/#?]+|[^-\w:\.\/#?]+/g)
		{ $match=$&;
		  next LEX if $match =~ /^[-\w:\.\/]/;
		  $match =~ s/./sprintf("%%%02x",ord($&))/eg;
		}
	    continue
		{ $sofar.=$match;
		}

	  if ($sofar ne $_)
		{ # print STDERR "html'quote($_) ==> ($sofar)\n";
		}

	  $sofar;
	}

sub unquote	# %xx -> raw
	{ local($_)=shift;
	  s/\%([\da-f]{2})/sprintf('%c',hex($1))/eig;
	  $_;
	}

sub ifunquote	# ["]quoted["] -> unquoted
	{ local($_)=shift;
	  # print STDERR "ifunquote($_) -> ";
	  s/^"(.*)"$/$1/;
	  $_=&unquote($_);
	  # print STDERR "[$_]\n";
	  $_;
	}

sub urls	# html -> (url,tag,...)
	{ local($_)=@_;
	  local(@urls,$anno,$url,$tag,$noslash,%attrs);
	
	  while (m:$annoptn:oi)
		{ $anno=$&;
		  $_=$';
		  ($tag,$noslash,%attrs)=&annodecode($anno);
		  if (defined($attrs{'href'}))
			{ $url=$attrs{'href'};
			  if ($tag eq 'a'
			   && $noslash
			   && /^([^>]*)<\s*\/\s*a\s*>/i)
				{ $tag=$1; $_=$'; }
			  else	{ $tag=''; }

			  push(@urls,$url,$tag);
			}
		}

	  @urls;
	}

sub annodecode	# "<[/]blah [foo=bar]...>" -> (blah,noslash,foo,bar,...)
	{ local($_)=shift;
	  local($type,$start,%attr,$a,$v);

	  return undef unless /^$annoptn/o;

	  $type=$1;
	  $_=$2;

	  if ($type =~ m:^/\s*:)	{ $start=0; $type=$'; }
	  else				{ $start=1; }

	  $type =~ tr/A-Z/a-z/;

	  ATTR:
	    while (1)
		{ if (/$annoattrvalptn/o) { $a=$1; $v=&ifunquote($2); }
		  elsif (/$annoattrptn/o) { $a=$1; undef $v; }
		  else			  { last ATTR; }

		  $_=$';
		  $a =~ tr/A-Z/a-z/;
		  $attr{$a}=(defined($v) ? $v : undef);
		}

	  ($type,$start,%attr);
	}

sub absurl	# (src_url,target_url) -> abs_url
	{ local($src,$target)=@_;

	  local($srcproto,$srchost,$srcport,$srcpath,$srclabel,$srcquery)
		=&urldecode($src);
	  local($tgtproto,$tgthost,$tgtport,$tgtpath,$tgtlabel,$tgtquery)
		=&urldecode($target);
	  local($url);

	  $tgtproto=$srcproto unless length($tgtproto);
	  ($tgthost,$tgtport)=($srchost,$srcport)
		unless length($tgthost);
	  if ($tgtpath !~ m:^/:)
		{ if ($srcpath =~ m:.*/:)
			{ $tgtpath=$&.$tgtpath;
			}
		}

	  $url=&mkurl($tgtproto,$tgthost,$tgtport,$tgtpath,$tgtlabel,$tgtquery);

	  $url;
	}

sub mkurl	# (proto,host,port,path,label,query) -> url
	{ local($proto,$host,$port,$path,$label,$query)=@_;
	  local($url);

	  $url='';
	  $url.="$proto:" if length($proto);
	  $url.="//$host" if length($host);
	  $url.=":$port" if length($port);
	  $url.=$path;
	  $url.="#$label" if length($label);
	  $url.="?$query" if length($query);

	  $url;
	}

sub urldecode	# url -> proto,host,port,path,label,query
	{ local($_)=shift;
	  local($proto,$host,$port,$path,$label,$query);

	  if (m|^(\w+)://|)
		{ $proto=$1; $_='//'.$'; }

	  if (m|^//([^/:#?]+)((:(\d+))?)|)
		{ $host=$1;
		  if (length($3))
			{ $port=$4+0;
			}

		  $_=$';
		}

	  /^[^#?]*/;
	  $path=$&;
	  $_=$';
	  if (/^#/)	{ $label=$'; }
	  elsif (/^\?/)	{ $query=$'; }

	  ($proto,$host,$port,$path,$label,$query);
	}

1;
