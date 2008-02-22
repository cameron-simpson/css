#!/usr/bin/perl
#
# Code to do JavaScript things (mostly output).
#	- Cameron Simpson <cs@zip.com.au> 17apr98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::JavaScript;

sub h2js($$)
	{ my($h,$obj)=@_;

	  my($type)=::reftype($h);
	  $type=SCALAR if ! defined $type;

	  my($js)="";

	  if ($type eq SCALAR)
		{
		  $js.="$obj=".squote($h);
		}
	  elsif ($type eq ARRAY)
		{ $js.="$obj=new Array";
		  
		  my($i);

		  for $i (0..$#$h)
			{ $js.=";\n".h2js($h->[$i],$obj."[$i]");
			}
		}
	  elsif ($type eq HASH)
		{ $js.="$obj=new Object";
		  
		  my($k);

		  for $k (sort keys %$h)
			{ $js.=";\n".h2js($h->{$k},$obj."[".squote($k)."]");
			}
		}
	  else
		{ warn "$::cmd: can't convert $type \"$h\" to javascript; treating as a string";
		  $js.="$obj=".squote($h);
		}

	  $js;
	}

sub squote
	{ local($_)=@_;

	  s/[\\']/\\$&/g;
	  "'$_'";
	}

sub dquote
	{ local($_)=@_;

	  s/[\\"]/\\$&/g;
	  "\"$_\"";
	}

1;
