#!/usr/bin/perl
#
# Replacement for the CGI package.
#	- Cameron Simpson <cs@zip.com.au>
#

=head1 NAME

cs::CGI - a replacement for the more common CGI module

=head1 SYNOPSIS

use cs::CGI;

=head1 DESCRIPTION

The cs::CGI module is a replacement for the CGI module
normally obtained from CPAN.
Originally written as a wrapper to work around its
extremely annoying "propagate query parameters to new form elements"
``feature'',
it is now a mostly complete replacement
with a very different approach to creating new forms.

=cut

use strict qw(vars);

BEGIN	{
	  use cs::DEBUG; cs::DEBUG::using(__FILE__);
	}

use cs::Misc;
use cs::HTML;
use cs::Source;
use cs::Sink;
use cs::RFC822;

# use CGI; ## not any more

package cs::CGI;

@cs::CGI::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=item tok2s(I<@markup>)

Shorthand call to the B<cs::HTML::tok2s> function,
to turn an HTML token structure into a text stream.

=cut

sub tok2s	{ cs::HTML::tok2s(@_) }

=item hexify(I<string>,I<pattern>)

Shorthand call to the B<cs::HTTP::hexify> function,
to replace special characters in I<string>
with %xx escapes.

=cut

sub hexify($$)	{ ::need(cs::HTTP); cs::HTTP::hexify($_[0],$_[1]) }

=item unhexify(I<string>)

Return I<string> with all %xx escapes converted into characters.

=cut

sub unhexify	{ local($_)=@_;
		  s/%([\da-f]{2})/chr(hex($1))/egi;
		  $_;
		}

sub _plusDecode	{ local($_)=@_; s/\+/ /g; $_ }
sub _plusEncode	{ local($_)=@_; s/\s/+/g; $_ }

sub _kwDecode	{ local($_)=@_;
		  map(unhexify(_plusDecode($_)),split(/\&/));
		}
sub _kwEncode	{ join('&',map(_plusEncode(hexify($_,'= \w')),@_)); }

sub _qsDecode	{ my($q)={};
		  my(@k)=_kwDecode(@_);

		  for (@k)
			{ if (/^(\w+)=/)
				{ $q->{uc($1)}=$';
				}
			}

		  $q;
		}
sub _qsEncode	{ my($q)=@_;
		  _kwEncode(map("$_=$q->{$_}",keys %$q));
		}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::CGI I<source>, I<env>, I<sink>

This creates a new cs::CGI object
from the query parameters in I<source>,
the environment variables in I<env>,
and with default output
(for the B<Print> method)
of I<sink>.

Any of these may be omitted or B<undef>,
defaulting to:
B<STDIN> for I<source>,
B<%ENV> for I<environment>
and B<STDOUT> for I<sink>.

=cut

sub new
{ my($class,$src,$env,$sink)=@_;
  $src=new cs::Source (FILE,STDIN) if ! defined $src;
  $env=\%ENV if ! defined $env;
  $sink=new cs::Sink (FILE,STDOUT) if ! defined $sink;

  my($_Q)={};
  my($H)=new cs::RFC822;

  my($Q)=bless { #### _CGI	=> $_Q,
		 HDRS		=> $H,
		 COOKIES	=> {},
		 HEAD		=> {},
		 HEAD_MARKUP	=> [],
		 HEAD_ATTRS	=> {},
		 BODY_ATTRS	=> {},
		 SOURCE		=> $src,
		 SINK		=> $sink,
	       }, $class;

  # default content type for return
  $Q->ContentType('text/html');

  $Q->{ENV}=$env;
  if ($Q->Method() eq GET)
  { $Q->{QUERY}=_qsDecode($Q->Query_String());
    ## warn "CGI GET got [".$Q->Query_String()."]";
  }
  else
  { ## warn "handling method [".$Q->Method()."] from STDIN";
    local($_);

    ## warn "env=".cs::Hier::h2a($env,0);

    if ($env->{CONTENT_TYPE} eq 'application/x-www-form-urlencoded')
	{
	  my(@getargs);
	  push(@getargs,$env->{HTTP_CONTENT_LENGTH}+0)
		if exists $env->{HTTP_CONTENT_LENGTH};

	  my($input)=scalar($src->Get(@getargs));
	  ## warn "CGI POST got [$input]";
	  $input =~ s/\s+$//;
	  $Q->{QUERY}=_qsDecode($input);
	}
    else
	{ warn "can't handle POSTs of type \"$env->{CONTENT_TYPE}\"";
	  $Q->{QUERY}={};
	}
  }

  # cookies
  my($cookies);

  if (defined ($cookies=$Q->Env(HTTP_COOKIE)))
  {
    for (split(/\s*;\s*/,$cookies))
    { if (/\s*(\S+)=(.*\S)/)
      { my($cookie,$value)=($1,$2);
	$value =~ s/%([\da-f]{2})/chr(hex($1))/egi;
	$Q->{COOKIES}->{$cookie}=$value;
      }
    }
  }

  $Q;
}

=back

=head1 OBJECT METHODS

=over 4

=item Env(I<key>)

Return the value named I<key>
from the environment.
If I<key> is omitted,
return the environment hashref itself in a scalar context
or the list of key values in an array context.

=cut

sub Env($;$)
{ my($Q,$key)=@_;
  my($E)=$Q->{ENV};
  if (! defined $key)
  { return wantarray ? keys %$E : $E;
  }

  return undef if ! defined $E->{$key};

  $E->{$key};
}

=item Query()

Return the query parameter hashref.

=cut

sub Query($)	{ shift->{QUERY} }

=item Values()

Return the query parameter hashref in a scalar context
or the list of parameter names in an array context
(for use with B<Value>, below).

=cut

sub Values($)
{ my($v)=shift->Query();
  wantarray
	? keys %$v
	: $v;
}

=item Value(I<key>,I<default>)

Return the value of the parameter named I<key>
or I<default> if the parameter was not present.
Returns the textual value of the parameter in a scalar context.
In an array context the value is split on whitespace into a list of words.
If the parameter is not present and I<default> is omitted,
returns B<undef> or B<()> depending on context.

=cut

sub Value($$;$)
{ my($Q,$field,$dflt)=@_;
  $field=uc($field);
  my($V)=$Q->Query();

  if (! exists $V->{$field} || ! defined $V->{$field})
  { return $dflt if defined $dflt;
    return wantarray ? () : undef;
  }

  ## warn "Value($field)=[$V->{$field}]";
  wantarray ? split(/\s+/,$V->{$field}) : $V->{$field};
}

=item Method()

Return the HTTP method used to call this CGI
(B<GET> or B<POST>).

=cut

sub Method	{ shift->Env(REQUEST_METHOD) }
sub Keywords	{ keys %{shift->{QUERY}} }
sub Query_String{ unhexify(shift->Env(QUERY_STRING)) }
sub ScriptURL	{ shift->Env(SCRIPT_NAME); }
# URL for script, with PATH_INFO, without query
sub FullURL	{ my($this)=@_;
		  my($E)=scalar $this->Env();

		  ## warn "FullURL: E=".cs::Hier::h2a($E,0);

		  my($url)="http://".$E->{SERVER_NAME};

		  $url.=":$E->{SERVER_PORT}" if defined $E->{SERVER_POR}
					     && length $E->{SERVER_PORT};

		  $url.=$E->{SCRIPT_NAME};
		  $url.=$E->{PATH_INFO} if exists $E->{PATH_INFO};

		  $url;
		}
# URL to repeat this query
sub SelfURL
{ my($this)=shift;
  $this->SelfQuery($this->Query());
}

sub SelfQuery
{ my($this,$q)=@_;

  ## warn "q=".cs::Hier::h2a($q,0)."<BR>\n";

  defined $q && ref $q && keys %$q
  ? $this->FullURL()."?"._qsEncode($q)
  : $this->FullURL();
}
# return PATH_INFO string, or components if in array context
sub PathInfo
{ my($this)=shift;
  my($path);
  $path=$this->Env(PATH_INFO);
  return $path if ! wantarray;
  grep(length,split(m:/+:,$path));
}

#sub _getCGIParams
#	{ my($_Q,@p)=shift;
#	  @p=$_Q->param() if ! @p;
#
#	  my($p)={};
#
#	  for (@p)
#		{ $p->{uc($_)}=[ $_Q->param($_) ];
#		}
#
#	  $p;
#	}
#sub _snuffCGIParams
#	{ my($_Q,@p)=@_;
#	  for (@p) { $_Q->delete($_); }
#	}

sub Param
{ my($F,$p,@v)=@_;
  die "Param(\$p=\"$p\") - not a word!" unless $p =~ /^\w+/;
  my($Q)=$F->{Q};

  if (! @v)
	{ return undef if ! exists $Q->{$p};
	  return @{$Q->{$p}} if wantarray;
	  return "@{$Q->{$p}}";
	}

  $Q->{$p}=[ @v ];
}

sub Form
{ my($this,$action)=(shift,shift);
  $action=$this->ScriptURL() if ! defined $action;

  ::need(cs::HTML::Form);
  cs::HTML::Form->new($action,@_);
}

# encode / in key (for subkey code (see cs::Hier::apply())) as _ and _ as __
sub _encKey
{ local($_)=@_;
  s/(_|\/+)/($& eq '_' ? '__' : '_')/eg;
  $_;
}
sub _decKey
{ local($_)=@_;
  s/((__)*)_/$1\//g;
  s/__/_/g;
  $_;
}

# take request record, a scheme and a record and emit a form for
# adjusting the record, controlling only the fields in the scheme
sub MkForm
{ my($this,$action,$schema,$record,$context)=@_;
  die "no action!" if ! defined $action;
  die "no scheme!" if ! defined $schema;
  die "no record!" if ! defined $record;
  $context={} if ! defined $context;

  ::need(cs::HTML::Form);
  my($F)=cs::HTML::Form->new($action,POST);

  my(@table)=();

  my($html,$desc,$type,$def,$fkey,@k,$value,@edit);

  my(@keys)=(exists $schema->{ALL_KEYS}
		? @{$schema->{ALL_KEYS}}
		: sort keys %$schema);

  for (@keys)
  {
    $fkey=EDIT_._encKey($_);
    @k=split(m:/+:);
    $value=cs::Hier::getSubKey($record,@k);
    warn "value[@k]=[$value]<BR>\n";

    $def=$schema->{$_};
    ($desc,$type)=($def->{DESC},$def->{TYPE});

    # prepare to collect field markup
    $F->StackMarkUp();

    push(@edit,$_);
    if ($type eq TEXTFIELD){ $F->TextField($fkey,$value); }
    elsif ($type eq TEXTAREA){$F->TextArea($fkey,$value); }
    elsif ($type eq STRINGS)
	  { $F->TextArea($fkey,join("\n", sort @$value));
	  }
    elsif ($type eq KEYWORDS)
	  { my($map,$pickone);
	    $map=(exists $def->{MAP} ? $def->{MAP} : {});
	    $pickone=(exists $def->{PICKONE}
			  ? $def->{PICKONE}
			  : 0);

	    warn "fkey=$fkey, pickone=$pickone";
	    if (! keys %$map)
		  # newline separated list
		  { $F->TextArea($fkey,join("\n", sort @$value));
		  }
	    elsif ($pickone)
		  { warn "POPUP for $fkey";
		  $F->Popup($fkey,$map,$value);
		  }
	    else
	    { warn "SCROLLINGLIST for $fkey";
	    $F->ScrollingList($fkey,$map,$value,[],15,1);
	    }
	  }
    else
    { warn "unknown type \"$type\" for field \"$_\" ($desc)";
      pop(@edit);
    }

    # collect field markup
    $html=$F->StackedMarkUp();

    # make table row with desc and markup
    push(@table,[TR,[TD,{ VALIGN => TOP },$desc],
		    [TD,{ VALIGN => TOP },@$html],
		    "\n"]);
  }

  # save original state
  $F->Hidden('ORIGINAL_RECORD',cs::Hier::h2a($record,0));
  $F->Hidden('KEYS',join(',',@edit));
  $F->Hidden('CONTEXT',cs::Hier::h2a($context,0));
  warn "state=".cs::Hier::h2a($context,0)."<BR>\n";

  # make table
  $F->MarkUp([TABLE,@table]);

  $F->Close();
}

# take the submission from a form created with the above method
# and return an Hier::diff() difference structure for application
# to a record
sub GetEdits
{ my($this,$query,$schema)=@_;
  $query=$this->Query() if ! defined $query;

  warn "GetEdits: schema:\n".cs::Hier::h2a($schema,1)
	if defined $schema;

  $schema={} if ! defined $schema;

  my($context)=cs::Hier::a2h($query->{CONTEXT});
  my(@keys)=grep(length,split(/\s*,\s*/,$query->{KEYS}));
  my($original)=cs::Hier::a2h($query->{ORIGINAL_RECORD});
  my($fkey,$value,@k,$type,$nvalue);
  my($diff)={};

  KEY:
    for (@keys)
    { $fkey=EDIT_._encKey($_);
      next KEY if ! exists $query->{$fkey};

      $type=(exists $schema->{$_}
		    ? $schema->{$_}->{TYPE}
		    : TEXTFIELD);
      warn "_=[$_], type=$type";

      @k=split(m:/+:);
      $value=cs::Hier::getSubKey($original,@k);

      $nvalue=($type eq KEYWORDS
	       ? [ grep(length,
			split(/[ \t\r\n]+/, $query->{$fkey})) ]
	       : $type eq STRINGS
		 ? [ grep(length,
			  split(/\s*\n(\s*\n)*\s*/, $query->{$fkey})) ]
		 : $query->{$fkey});

      warn "nvalue[$_]=".cs::Hier::h2a($nvalue) if $type eq KEYWORDS;

      if (! defined $value
       || cs::Hier::hcmp($query->{$fkey},$value) != 0)
	    { $diff->{$_}=$nvalue;
	    }
    }

  ($diff,$context);
}

sub HdrAdd
{ my($this)=shift;
  $this->{HDRS}->Add(@_);
}
sub HdrSet { HdrAdd(@_,REPLACE); }

sub ContentType
{ my($this,$type,$params)=@_;
  $params={} if ! defined $params;

  if (keys %$params)
	{ $type=join('; ',
		     $type,map("$_=$params->{$_}",
			       sort keys %$params));
	}

  $this->HdrSet([CONTENT_TYPE,$type]);
}

# save singular <HEAD> attribute
sub HeadInfo
{ my($this)=shift;
  my $mark = cs::HTML::mkTok(@_);
  $this->{HEAD}->{uc($mark->{TAG})}=$mark;
}

# append markup to <HEAD>
sub HeadMarkUp
{ my($this)=shift;
  push(@{$this->{HEAD_MARKUP}},@_);
}

sub HeadAttr
{ my($this,%a)=@_;
  for my $a (keys %a)
  { $this->{HEAD_ATTRS}->{$a}=$a{$a};
  }
}

sub BodyAttr
{ my($this,%a)=@_;
  for my $a (keys %a)
  { $this->{BODY_ATTRS}->{$a}=$a{$a};
  }
}

sub SetCookie
{ my($this,$name,$value,$params)=@_;
  $params={} if ! defined $params;

  $params->{PATH}='/' if ! exists $params->{PATH};

  # XXX - translate expiry terms into valid expiry string

  $this->HdrAdd([SET_COOKIE,
		 join('; ',
		       "$name="
		       .hexify($value,HTML),
		       map(lc($_)."=$params->{$_}",sort keys %$params))
		]);
}
sub Cookies
{
  my($this)=@_;

  $this->{COOKIES};
}

sub Print	# (sink,\@html,need_tok2a) -> void
{
  my($this,$html,$need_tok2a,$sink)=@_;
  $need_tok2a=1 if ! defined $need_tok2a;
  $sink=$this->{SINK} if ! defined $sink;

  if (! ref $sink)
  # assume we got a FILE
  { my($FILE)=$sink;
    warn "attach sink to $sink";

    $FILE=caller(0)."::$FILE" unless $FILE =~ /('|::)/;
    $sink=new cs::Sink (FILE, $FILE);
  }

  ## warn "sink=[$sink]";
  ## warn "preHTML=".cs::Hier::h2a($html,0);

  $this->{HDRS}->WriteItem($sink);

  if ($need_tok2a)
  {
    # complete construction of <HEAD>

    # add <TITLE> if missing
    if (! exists $this->{HEAD}->{TITLE})
    { my(@h1)=cs::HTML::grepMarkUp(H1,@$html);
      if (@h1)
      { ## my(@c)=caller;
	## warn "h1=[@h1] from [@c]";

	my $firstH1 = $h1[0];
	my $htype = ::reftype($firstH1);
	my @tokens = ($htype eq ARRAY
		       ? @$firstH1[1..$#$firstH1]
		       : @$firstH1->{TOKENS}
		     );

	## warn "tok=[@$firstTok]";
	shift(@tokens) if $htype eq ARRAY && ::reftype($tokens[0] eq HASH);

	$this->HeadInfo(TITLE,cs::HTML::tokFlat(@tokens));
      }
    }

    # put all <HEAD> tags into HEAD_MARKUP
    for (keys %{$this->{HEAD}})
    { $this->HeadMarkUp($this->{HEAD}->{$_});
    }
    $this->{HEAD}={};

    # write complete HTML item
    tok2s(1,$sink,
		  [HTML,
		    [HEAD,
		      $this->{HEAD_ATTRS},
		      @{$this->{HEAD_MARKUP}}],
		    [BODY,
		      $this->{BODY_ATTRS},
		      @$html],
		  ]
	  );
  }
  else
  { $sink->Put(@$html);
  }
}

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
