#!/usr/bin/perl
#
# Object to talk about an HTML document.
#	- Cameron Simpson <cs@zip.com.au> 15oct94
#

use strict qw(vars);

use cs::HTML;

package cs::HTML::Doc;

require Exporter;
@cs::HTML::Doc::ISA=qw(Exporter);
@cs::HTML::Doc::EXPORT_OK=qw(TABLE TD TR IMG HREF);

sub new
	{ my($class,$src,$url)=@_;

	  bless { URL	=> $url,
		  DS	=> $src,
		}, $class;
	}

sub Tokens
	{ my($this)=shift;

	  return $this->{TOKENS} if exists $this->{TOKENS};

	  $this->{TOKENS}={};
	}

1;
