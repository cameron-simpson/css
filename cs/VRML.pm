#!/usr/bin/perl
#
# Code to parse and render VRML.
#	- Cameron Simpson <cs@zip.com.au> 30dec96
# VERY INCOMPLETE!!
#

use strict qw(vars);

BEGIN { use DEBUG; DEBUG::using(__FILE__); }

use cs::Tokenise;

package cs::VRML;

@cs::VRML::ISA=qw();

# syntactic classes
$cs::VRML::_ptn_node_name='[^\d\s\000-\037'."'".'"\\{}+.][^\s\000-\037'."'".'"\\{}+.]*';

sub new
	{ my($class,$type)=(shift,shift);

	  if ($type eq Source)
		{ return _new_Source($class,@_);
		}

	  die "can't make new cs::VRML of type \"$type\"";
	}

sub _new_Source
	{ my($class,$s)=@_;

	  my($node);

	  return undef if ! defined ($node=_parse($s));

	  bless $node, $class;
	}

sub _parse
	{ my($s)=shift;
	  my($t)=new cs::Tokenise ($s,\&_tokenise);

	  my($this)={};

	  local($_);

	  return undef if ! defined ($_=$t->Tok());

	  if (! /^$_ptn_node_name$/o)
		{ warn "syntax error: expected node_name, got \"$_\"";
		  return undef;
		}

	  $this->{NAME}=$_;

	  $this;
	}

sub _tokenise
	{ local($_)=shift;

	  # strip whitespace and comments
	  while (s/^\s+// || s/^#[^\n\r]*[\n\r]//)
		{}

	  # look for a name, punctuation, or a number
	  if (/^($_ptn_node_name|[{}]|-?\d+(\.\d+)|"([^\\"]|\\[\\"])*")/o)
		{ return ($&,$');
		}

	  undef;
	}


1;
