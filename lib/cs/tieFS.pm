#!/usr/bin/perl
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::tieFS;

$cs::tieFS::Path="\0path";

sub new		# (dir)
	{ local($dir)=@_;
	  local($r)=bless {	$Path => $dir };

	  $r;
	}

sub DESTROY
	{ local($this)=shift;

	  rmdir($this->{$Path})
		|| warn "rmdir($this->{$Path}): $!";
	}

sub FETCH
	{ local($this,$key)=@_;
	  local($path)="$this->{$Path}/$key";
	  local(@a,$_);

	  stat($path) || return undef;

	  if (-f _)	{ open(FETCH,"< $path\0") || return undef;
			  @a=<FETCH>;
			  close(FETCH);

			  return wantarray ? @a : join('',@a);
			}

	  if (-d _)	{ return new $path;
			}

	  warn "FETCH($this->{$Path},$key): $path: unsupported filetype\n";
	  return undef;
	}

sub STORE
	{ local($this,$key,$value)=@_;
	}
