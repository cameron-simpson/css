#!/usr/bin/perl
#
# Code to support simple and hopefully space-efficient indices
# of text.	- Cameron Simpson <cs@zip.com.au> 04jul94
#
# Index format:
#  idx	Flat file with keywords and offset to references in refs.
#  refs	Flat file with keyword references:
#			file#line,... file#line,...
#

require 'cs/open.pl';
use Upd;
sub out { cs::Upd::out(@_); }
sub err { cs::Upd::err(@_); }

package ptx;

undef $REFS;
$ptxname='.PTX';
$changed=0;

# save word references from a line
sub words	# (ref,line) -> void
	{ local($ref,$_)=@_;
	  local($key);

	  err '_';
	  WORD:
	    while (length)
		{ next WORD if s/^\s+//;
		  if (/^<(\w[^>\s@]*@[\w.-]+)>/		# msgid or email addr
		   || /^([a-z]+:\/\/\S+)/		# URL
		   || /^([a-zA-Z][^>\s@]*@[\w.-]+)/	# email addr
		   || /^([a-zA-Z]\w*)/			# word
		     )
			{ $key=$1;
			  $_=$';
			  $key =~ tr/A-Z/a-z/;
			  if (!defined($IGN{$key}))
				{ &addkey($key,$ref);
				}
			}
		  else
		  { substr($_,$[,1)='';
		  }
		}
	}

# return keys in offset order
sub keys
	{ &sync;
	  keys %offset;
	}

sub keysbyoffset { sort byoffset &keys; }

sub ndxnames		{ (&idxname,&refsname); }
sub idxname		{ $_[0].'/idx.Z'; }
sub refsname		{ $_[0].'/refs'; }

# open the named index, return ok; sets $REFS and $ptxname
sub open	# (fname,needwrite) -> ok
	{ local($fname,$needwrite)=@_;
	  local($idx,$refs)=&ndxnames($fname);

	  if ($needwrite && ! -d "$fname/." && ! mkdir($fname,0777))
		{ err "$'cmd: ptx'open: can't mkdir($fname): $!\n";
		  return undef;
		}

	  if (!defined($REFS=&'subopen(($needwrite ? '+>>' : '<'),$refs)))
		{ err "$'cmd: ptx'open: can't open $refs: $!\n";
		  return undef;
		}

	  $ptxname=$fname;

	  if (!open(IDX,"zcat 2>/dev/null <'$idx' |"))
		{ return 1 if $needwrite;	# empty index

		  err "$'cmd: ptx'open: can't read from $idx: $!\n";
		  &close;
		  return undef;
		}

	  local($_,$key,$ref,$ok);

	  err "reading index ...\n";
	  IDX:
	    while (<IDX>)
		{ if (!/^(\S+)\s+(\d+)/)
			{ err "$'cmd: ptx'open($idx): line $.: bad data: $_";
			  next IDX;
			}

		  $offset{$1}=$2+0;
		  if ($1 eq 'when') {$'DEBUG && err "offset{when}=$2\n"; }
		}

	  close(IDX);
	  err "done.\n";

	  1;
	}

sub close
	{ &sync;
	  close($REFS);
	  $'DEBUG && err "sub close($REFS)\n";
	  undef $REFS, $ptxname;
	}

# rewrite newkeys and index; uses $ptxname and $REFS
sub sync	# void -> ok
	{ return 1 if ! $changed;

	  local($key,@loadkeys,%saverefs);

	  @loadkeys=keys %newrefs;

	  # err "sync: #loadkeys = ", $#loadkeys+1, "\n";

	  # suck on old keys sequentially
	  # can't call &keysbyoffset because it calls &sync
	  err "collating changed refs ...\n";
	  for $key (sort byoffset @loadkeys)
		  { cs::Upd::out($key);
		    $saverefs{$key}=&ref($key);
		  }
	  cs::Upd::out('');

	  # err "rewriting [@loadkeys]\n";

	  if (!seek($REFS,0,2))
		{ err "$'cmd: ptx'sync: $REFS($ptxname): can't seek to end: $!\n";
		  return 0;
		}

	  err "appending changed refs ...\n";
	  for $key (keys %saverefs)
		{ $offset{$key}=tell($REFS);
		  print $REFS $saverefs{$key}, "\n";
		}

	  local($idx)=&idxname($ptxname);

	  if (!open(IDX,"| compress -v >'$idx'"))
		{ err "$'cmd: ptx'sync: can't rewrite $idx: $!\n";
		  return 0;
		}

	  err "rewriting index ...\n";
	  # can't call &keysbyoffset because it calls &sync
	  for $key (sort byoffset keys %offset)
		{ print IDX $key, ' ', $offset{$key}, "\n";
		}

	  close(IDX);

	  err "done.\n";

	  # flush cache
	  undef %newrefs;
	  $changed=0;

	  1;
	}

sub byoffset
	{ (defined($offset{$a}) ? $offset{$a} : 0)
      <=> (defined($offset{$b}) ? $offset{$b} : 0);
	}

sub addkey	# (key,newrefstr) -> void
	{ $changed=1;
	  $newrefs{$_[0]}.=' '.$_[1];
	}

# return full reference including update,
# cache full reference
sub ref	{ local($key)=shift;
	  local($_);

	  $_=&refs2refstr(&refstr2refs(&_ref($key).' '.$newrefs{$key}));
	  $'DEBUG && err "ref($key)=$_\n";
	  delete $offset{$key};
	  $newrefs{$key}=$_;
	}

# return existing key reference
sub _ref	# key -> refstr
	{ $'DEBUG && print "_ref($_[0]) ...\n";
	  return undef if ! defined($offset{$_[0]});

	  local($key)=shift;
	  local($_);

	  die "$'cmd: $ptxname: can't seek to ref{$key}: $!\n"
		unless tell($REFS) == $offset{$key}	# bypass seek (with
							# its associated stdio
							# buffer flush) if
							# possible
		    || seek($REFS,$offset{$key},0);

	  die "$'cmd: $ptxname: can't read ref{$key}: $!\n"
		unless defined($_=<$REFS>);

	  $'DEBUG && err "_ref($key)=$_\n";
	  chop;
	  $_;
	}

sub refstr2refs	# refstr -> @refs
	{ local($_)=@_;
	  local(%refs);

	  local(@refs,$ref,$reflist,$front,$back);

	  REFLIST:
	    for $reflist (grep(length,split))
		{ if ($reflist !~ /^([^#]+)#([^#]*)$/)
			{ err "$'cmd: ptx'refstr2refs: bad reflist: $reflist\n";
			  next REFLIST;
			}

		  $front=$1;
		  $back=$2;
		  for $ref (split(/,+/,$back))
			{ $refs{"$front#$ref"}=1;
			}
		}

	  keys %refs;
	}

sub refs2refstr
	{ local($_,$front,$back,$cur,$ref,%refs);

	  # err "refs2refstr(@_)";

	  for (@_)	{ $refs{$_}=1; }

	  $ref='';
	  for (sort keys %refs)
		{ if (/^([^#]+)#([^#]*)$/)
			{ ($front,$back)=($1,$2);
			  if (!defined($cur) || $cur ne $front)
				{ $cur=$front;
				  $ref.=" $cur#$back";
				}
			  else
			  { $ref.=",$back";
			  }
			}
		  else
		  { undef $cur;
		    $ref.=' '.$_;
		  }
		}

	  $ref =~ s/^\s+//;
	  # err " -> [$ref]\n";
	  $ref;
	}

sub uniq	# @list -> @uniquelist
	{ local(%_);
	  for (@_) {$_{$_}=1;}
	  keys %_;
	}

1;
