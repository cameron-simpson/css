#!/usr/bin/perl
#
# Parse and evaluate myke expressions.
#	- Cameron Simpson <cs@zip.com.au> 
#
# A parsed expression is either:
#  A scalar:	Its value.
#  A list ref:	The abutment of the evaluations of its elements.
#  A hash ref:	The evaluation of its meaning.
#
# Evaluation returns a list of values, which are permuted
# during abutment so that:
#    (a).(b,c).(d)
# makes (abd,acd).
# The hash has three elements:
#	FN	A sub ref to evaluate the operation.
#	ARGS	An array ref with the operands.
#	CONTEXT	The location in the source which generated this function.
#
# The FN is called as
#	FN(the-hash,@ARGS)
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Hier;

package cs::Myke::Expr;

@cs::Myke::Expr::ISA=qw();

sub e_expr	# (expr,dep) => array
	{ my($e,$dp)=@_;

	  if (! wantarray)
		{ my(@c)=caller;
		  die "e_expr called in non-array context from [@c]";
		}

	  return $e if ! ref $e;

	  my($reftype)=cs::Hier::reftype($e);

	  if ($reftype eq ARRAY)
		{ return _permute(@$e);
		}

	  # hash ref
	  &{$e->{FN}}($e,@{$e->{ARGS}});
	}

sub _permute
	{ my($v,$ve,@v2,$v3,$e);

	  $v=[""];
	  while (@_)
		{ $e=shift(@_);		# fetch expression
		  @v2=e_expr($e);	# evaluate
		  return () if ! @v2;	# short-circuit

		  # permute
		  $v3=[];
		  for $ve (@$v)
			{ push(@$v3,map($ve.$_,@v2));
			}

		  $v=$v3;		# replace old with new
		}

	  @$v;
	}

# (context,text,stoplist) -> (expr,tail)
sub parse
	{ my($context,$text,$stoplist)=@_;

	  if (! wantarray)
		{ my(@c)=caller;
		  die "not in array context from [@c]";
		}

	  local($cs::Myke::Expr::Context)=$context;
	  p_expr($text,$stoplist);
	}

sub p_expr
	{ if (! wantarray)
		{ my(@c)=caller;
		  die "not in array context from [@c]";
		}

	  local($_)=shift;
	  my($stoplist)=@_;
	  $stoplist="" if ! defined $stoplist;

	  my($ok)=1;

	  my(@elist)=();	# list of expressions (words) found
	  my(@word)=();		# list of subexprs to permute

	  my($o_)=$_;	# save original

	  EXPR:
	    while (length)
		{ last EXPR if index($stoplist,substr($_,0,1)) >= 0;

		  if (/^\s/)
			# end of word
			{ push(@elist,[@word]) if @word;
			  @word=();
			  last if ! /[\S$stoplist]/;	# skip trailing space
			  push(@elist,$`);		# gather space
			  $_=$&.$';			# proceed at next word
			  next EXPR;
			}

		  if (/^\$/)
			# subexpression
			{ my($subexpr,$tail)=p_subexpr($_,$stoplist);

			  if (! defined $subexpr)
				{ warn "$cs::Myke::Expr::Context: parse error at \"$_\"\n";
				  $ok=0;
				  last EXPR;
				}

			  push(@word,$subexpr);
			  $_=$tail;
			  next EXPR;
			}

		  /^[^\$\s$stoplist]+/
			|| die "logic error: stoplist=[$stoplist], \$_=[$_]";

		  push(@word,$&);
		  $_=$';
		}

	  push(@elist,[@word]) if @word;

	  ( $ok ? _fn(\&_e_JOIN,'',@elist) : undef,
	    $_
	  );
	}

sub _fn
	{ my($op)=shift;

	  { FN		=> $op,
	    ARGS	=> [ @_ ],
	    CONTEXT	=> $cs::Myke::Expr::Context,
	  }
	}

sub p_subexpr
	{ if (! wantarray)
		{ my(@c)=caller;
		  die "not in array context from [@c]";
		}

	  local($_)=@_;
	  my($o_)=$_;

	  /^\$/ || die "no \$ ? [$_]";

	  $_=$';

	  if (! length)
		{ warn "$cs::Myke::Expr::Context: \$ what? at end of line\n";
		  return (undef,$_);
		}

	  # single char macro
	  if (/^[a-zA-Z_]/)
		{ return ( _fn(\&_e_MACRO,$&), $' );
		}

	  # $$ => $
	  if (/^\$/)
		{ return ('$', $');
		}

	  # $(macro expr) or $((macro expr)) or ${} equiv
	  if (! /^[\(\{]/)
		{ warn "$cs::Myke::Expr::Context: syntax error after \$ at \"$_\"\n";
		  return (undef,$_);
		}

	  my($br)=$&;

	  my($macro,$perm,$param,@params,@modlist);

	  # multiple brackets?
	  $perm=(substr($_,0,1) eq $br);
	  if ($perm)	{ substr($_,0,1)=''; }
	  
	  s/^\s+//;

	  if (/^([a-z_]\w*)\s*/i)
		{ $macro=$1;
		  $_=$';

		  # parameter list
		  if (/^\(\s*/)
			{ $_=$';

			  ($param,$_)=parse($cs::Myke::Expr::Context,
					    $_,',)');
			  push(@params,$param);

			  while (/^,\s*/)
				{ $_=$';
				  ($param,$_)=parse($cs::Myke::Expr::Context,
						    $_,',)');
				  push(@params,$param);
				}

			  if (! /^\)\s*/)
				{ warn "$cs::Myke::Expr::Context: missing closing parenthesis for macro parameters at \"$_\"\n";
				  return (undef,$_);
				}

			  $_=$';
			}

		  $macro=_fn(\&_e_MACRO,$macro,@params);
		}
	  elsif (/^(")(([^\\"]|\\.)*)"\s*/
	      || /^(')(([^\\']|\\.)*)'\s*/)
		{ $macro=$2;
		  $_=$';
		  $macro =~ s/\\(.)/$1/g;
		}
	  else
	  { warn "$cs::Myke::Expr::Context: syntax error (expected string or macro name) at \"$_\"\n";
	    return (undef,$_);
	  }

	  s/^\s*//;	# skip to modifiers

	  warn "missing MODIFIER parse";

	  my($closebr)=(($br eq '(' ? ')' : '}') x ($perm ? 2 : 1));
	  my($mod);

	  while (length && substr($_,0,length $closebr) ne $closebr)
		{ $mod=substr($_,0,1);
		  substr($_,0,1)='';

		  if (0)	{ }
		  else		{ warn "$cs::Myke::Expr::Context: unrecognised modifier '$mod' at \"$mod$_\"\n";
				  return (undef,$_)
				}

		  s/^\s+//;
		}

	  if (substr($_,0,length $closebr) ne $closebr)
		{ warn "$cs::Myke::Expr::Context: missing closing \"$closebr\" at \"$_\"\n";
		  return (undef,$_);
		}

	  substr($_,0,length $closebr)='';

	  my($subexpr)=_fn(\&_e_MODIFY,$macro,@modlist);
	  if (! $perm)
		{ $subexpr=_fn(\&_e_JOIN(' ',$subexpr));
		}

	  ($subexpr,$_);
	}

########################################
# operators
sub _e_JOIN
	{ my($e,$sep)=(shift,shift);
	  join($sep,map(e_expr($_),@_));
	}

sub _e_MODIFY
	{ my($e,$macro,@modlist)=@_;

	  warn "macro=".cs::Hier::h2a($macro,1)."\nmodlist=[@modlist]";

	  my(@mvalue)=e_expr($macro);

	  "@mvalue";
	}

sub _e_MACRO
	{ my($e,$macro,@params)=@_;

	  "\$$macro(@params)";
	}

1;
