#!/usr/bin/perl
#
# A mailrc object.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Mail::Aliases;
use cs::Source;
use cs::Pathname;
use cs::Misc;

package cs::Mail::Rc;

@cs::Mail::Rc::ISA=qw();

sub new
	{ my($class)=@_;

	  bless { ALIASES => new cs::Mail::Aliases,
		}, $class;
	}

sub Aliases
	{ keys %{shift->{ALIASES}};
	}
sub Alias
	{ my($this)=shift;
	  $this->{ALIASES}->Value(@_);
	}
sub ExpandAlias
	{ my($this)=shift;
	  $this->{ALIASES}->Expand(@_);
	}
sub ExpandAliasText
	{ my($this,$text,$sep)=@_;
	  $sep=', ' if ! defined $sep;

	  my($exp)=$this->ExpandAlias($text);
	  join($sep,map($exp->{$_},keys %$exp));
	}

sub newEntry
	{ my($r,$key)=@_;
	  my($this);

	  if (! ref $r)
		{ $r=[$r];
		}

	  if (::reftype($r) eq ARRAY)
		{ $this={ ADDRS => {} };
		  map( $this->{ADDRS}->{$_}={ TAGS => [] }, @$r );
		}
	  else
	  {
	    my($v);

	    for (keys %$r)
		{ $v=$r->{$_};
		  if (! ref $v)
			{ $r->{$_}={ TAGS => [$v] };
			}
		  elsif (::reftype($v) eq ARRAY)
			{ $r->{$_}={ TAGS => $v };
			}

		  $r->{$_}->{TAGS}=[] if ! exists $r->{$_}->{TAGS};
		}
	  }

	  $this->{KEY}=$key;

	  bless $this, cs::Mail::Rc;
	}

sub AddEntry
	{ my($this,$A)=@_;

	  my($addr,$aval,$sfx,$n);

	  $n=0;
	  for $addr (keys %{$this->{ADDRS}})
		{ $aval=$this->{ADDRS}->{$addr};
		  if (! ref $aval)
			{ $sfx=$aval;
			}
		  elsif (exists $aval->{TAGS})
			{ $sfx=$aval->{TAGS};
			}
		  else	{ $n++;
			  $sfx=($n > 1 ? $n : '');
			}

		  $A->Add($_,$aval->{ADDRTEXT},0);
		  $A->Add($_.$sfx,$aval->{ADDRTEXT},0);
		}
	}

sub LoadDB
	{ my($this,$path,$force)=@_;
	  $force=0 if ! defined $force;

	  return undef if ! -e $path;

	  ::need(cs::Persist);

	  my($db)=cs::Persist::db($path);

	  return undef if ! defined $db;

	  $this->{ALIASES}->AddDB($db);
	}

sub Load
	{ my($this,$rc,$force)=@_;
	  $force=0 if ! defined $force;

	  my($path);

	  if (! ref $rc)
		{ $path=$rc;
		  $rc=new cs::Source (PATH,$path);
		  return undef if ! defined $rc;
		}
	  else	{ $path=$rc->{PATH};
		}

	  local($_);
	  my($lineno)=0;
	  my($line,$sublineno,$context);

	  MAILRC:
	    while (defined ($_=$rc->GetLine()) && length)
		{ $lineno++;
		  $sublineno=0;
		  $context="$path, line $lineno";

		  chomp;

		  while ($_ =~ /\s*,\s*\\?$/)	# slosh/comma extension
			{ $_="$`,";
			  if (defined ($line=$rc->GetLine()) && length $line)
				{ $sublineno++;
				  chomp($line);
				  $line =~ s/^\s+//;
				  $_.=' '.$line;
				}
			  else
			  { warn "$context: unexpected EOF in line continuation\n";
			    last MAILRC;
			  }
			}

		  s/^#.*//;			# strip comments
		  s/\s+#.*//;			# strip comments
		  s/^\s+//;
		  s/\s+$//;

		  next MAILRC unless /^(\S+)\s*/; # skip blank lines
		  $_=$';

		  if ($1 eq 'alias')
			{ if (!/^(\S+)\s+(\S.*)/)
				{ warn "incomplete alias\n";
				  next MAILRC;
				}

			  $this->{ALIASES}->Add($1,$2,$force,$context);
			}
		  elsif ($1 eq 'include')
			{ my($inc);

			  for $inc (grep(length,split(/\s+/)))
				{ $inc =~ s/^~\w*/cs::Pathname::untilde($&)/e;
				  if ($inc =~ m:^[^/]:)
					{ $inc=cs::Pathname::dirname($path)."/$inc";
					}

				  $this->Load($inc);
				}
			}
		  else
		  { warn "$context: unknown directive \"$1\" ignored\n";
		  }
		}
	  continue
		{ $lineno+=$sublineno;
		}
	}

1;
