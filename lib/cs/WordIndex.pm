#!/usr/bin/perl
#
# Maintain keyword indices.
#	- Cameron Simpson <cs@zip.com.au> 24apr2002
#

=head1 NAME

cs::WordIndex - a keyword index

=head1 SYNOPSIS

	use cs::WordIndex;

	$I = new cs::WordIndex("indexfile.gz");
	$I->ProcessFile("textfile");
	$I->Save();

=head1 DESCRIPTION

The cs::WordIndex module
defines a keyword index object,
with methods for adding files to the index and searching the index.

Both the index files and the textfiles may be compressed.

The main index format consists of one line per keyword, of the form

	keyword hits...

where hits is a space separated list of "file/lines" pairs, where "file" is
a pathname relative to the directory of the index file, and "lines" is a
comma separated list of lines in which the keyword appears.
Adjacent line citations may be coalesced into ranges "n-m".

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::WordIndex;

=head1 GENERAL FUNCTIONS

=over 4

=item fetchCollatedResults(I<collatedResults>)

Take some search results after collation by the B<CollateResults> method
and fetch the lines involved,
returning an arrayref for results,
each an arrayref of the form B<I<file>, I<lineno>, I<line>, I<words...>]>.

=cut

sub fetchCollatedResults
{ my($fmap)=@_;

  my @fetched;

  my $lmap;
  my $flines;
  my @lines;
  my $lineno;

  FILE:
  for my $file (sort keys %$fmap)
  {
    if (! cs::Misc::openr(cs::WordIndex::TEXTFILE, $file))
    { warn "$::warningContext: can't open $file: $!\n";
      next FILE;
    }

    local($::warningContext)="$::warningContext: $file";

    $flines = $fmap->{$file};
    @lines=sort {$a<=>$b} keys %$flines;

    FETCHLINE:
    while (@lines)
    { $lineno=shift(@lines);

      LINE:
      while ($. < $lineno)
      { $_=<TEXTFILE>;
	if (! defined)
	{ warn "$::warningContext:$.: unexpected EOF\n";
	  last FETCHLINE;
	}
      }

      chomp;

      # stash line and word hits
      push(@fetched,[ $file, $lineno, $_, @{$flines->{$lineno}} ]);
    }
    close(TEXTFILE);
  }

  return \@fetched;
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::WordIndex(I<indexfile>,I<bigmode>)

Create a new WordIndex object
attached to the file named I<indexfile> (if specified).
The optional parameter I<bigmode>,
if specified and true,
turns on deferred update mode
where index updates are queued for application at index B<Save()> time.

=cut

sub new
{ my($class,$filename,$bigmode)=@_;
  $bigmode=0 if ! defined $bigmode;

  my $this = bless { NDX => {}, BIGMODE => $bigmode, Q => [] }, $class;

  if (defined $filename)
  { $this->{INDEXFILE}=$filename;
    $this->LoadIndex($filename);
  }

  return $this;
}

=back

=head1 OBJECT METHODS

=over 4

=cut

sub _BigMode
{ shift->{BIGMODE};
}

=item ProcessFile(I<filename>)

Read the text available in the file named I<filename>
and add it to the index.

=item ProcessFile(I<FILE>, I<filename>)

Read the text available on the input stream I<FILE>
and add it to the index.
I<filename> is used in error reports.

=cut

sub ProcessFile
{ my($this,$FILE,$file)=@_;

  if (@_ == 2)
  { 
    $file=$FILE;
    if (! cs::Misc::openr(cs::WordIndex::TEXTFILE,$file))
    { warn "$::warningContext: can't open \"$file\": $!\n";
      return;
    }

    $FILE=TEXTFILE;
  }

  local($_);
  my $lineno = 0;
  my @w;

  LINE:
  while (defined($_=<$FILE>))
  { $lineno++;
    chomp;
    s/^\s+//;
    next LINE if ! length;

    @w=();

    # pure words (well, including underscores)
    for my $word (::uniq(map(lc,grep(length,split(/[^_\w]+/)))))
    { push(@w,$word);
    }

    # compound words with dashes
    for my $word (::uniq(map(lc,grep(length,split(/[^-\w]+/)))))
    { $word =~ s/^-+//;
      $word =~ s/-+$//;
      push(@w,$word);
    }

    # compound words with dots
    for my $word (::uniq(map(lc,grep(length,split(/[^.\w]+/)))))
    { $word =~ s/^\.+//;
      $word =~ s/\.+$//;
      push(@w,$word);
    }

    # compound words with dashes and underscores and dots
    for my $word (::uniq(map(lc,grep(length,split(/[^-_.\w]+/)))))
    { $word =~ s/^[-.]+//;
      $word =~ s/[-.]+$//;
      push(@w,$word);
    }

    WORD:
    for my $word (::uniq(@w))
    { next WORD if length($word) < 2 || length($word) > 32;
      next WORD if $word =~ /^\d{1,3}$/;	# toss smaller numbers
      $this->AddWord($word,$file,$lineno);
    }
  }
}

=item AddWord(I<word>,I<filename>,I<lineno>)

Add the keyword I<word> to the index as at line I<lineno> of the file I<filename>.

=cut

sub AddWord
{ my($this,$word,$file,$lineno)=@_;
  if ($this->_BigMode())
  { push(@{$this->{Q}->{$word}}, $file, $lineno);
  }
  else
  { _addToWordIndex($this->_WordIndex($word),$file,$lineno);
  }
}

sub _addToWordIndex($$$)
{ my($wr,$file,$lineno)=@_;

  $wr->{$file}=[] if ! exists $wr->{$file};

  push(@{$wr->{$file}}, $lineno);
}

=item WordIndex(I<word>,I<uniq>)

Return an hashref of the occurences of the specified word
in the indexed files.
The keys of the hashref are filenames
and the values are arrayrefs containing line numbers.
The optional parameter I<uniq>,
if supplied and true,
ensures the line number arrayrefs contain no duplicates.

=cut

sub WordIndex
{ my($this,$word,$uniq)=@_;

  my $wr = $this->_WordIndex($word);

  if (defined $uniq && $uniq)
  { my $ary;
    for my $file (keys %$wr)
    { $ary=$wr->{$file};
      $wr->{$file}=[ ::uniq(@$ary) ] if @$ary > 1;
    }
  }

  return $wr;
}

sub _WordIndex
{ my($this,$word)=@_;

  my $ndx = $this->{NDX};

  if (! exists $ndx->{$word})
  { $ndx->{$word}={};
  }
  elsif (! ref $ndx->{$word})
  { $ndx->{$word}=_decodeIndexLine($ndx->{$word});
  }

  return $ndx->{$word};
}

=item Save(I<filename>)

Save the index to the file named I<filename>,
or to the file specified when this index object was created if I<filename> is omitted.

=cut

sub Save()
{ my($this,$ndxfile)=@_;
  if (! defined $ndxfile)
  { if (! exists $this->{INDEXFILE})
    { die "$::warningContext: no default index filename";
    }

    $ndxfile=$this->{INDEXFILE};
  }

  if (! cs::Misc::openw(cs::WordIndex::INDEX,$ndxfile))
  { warn "$::warningContext: can't write to $ndxfile: $!\n";
    return;
  }

  my $ndx = $this->{NDX};
  my($r,$ary);

  WORDINDEX:
  for my $word (sort keys %$ndx)
  { print INDEX $word;

    $r = $ndx->{$word};
    if (! ref $r)
    # line never decoded - just echo it back into the file
    { print INDEX " $r\n";
      next WORDINDEX;
    }

    FILE:
    for my $file (sort keys %$r)
    { $ary = $r->{$file};
      next FILE if ! @$ary;
      print INDEX " $file/", _range2txt(@$ary);
    }

    print INDEX "\n";
  }

  close(INDEX);
}

sub _range2txt
{
  my $txt = "";
  my $next;	# next item - to measure continuity
  my $last;	# last uncommited number

  for my $n (sort {$a <=> $b} &::uniq(@_))
  { if (! defined $next || $next < $n)
    { # commit pending range, if any
      if (defined $last)
      { $txt.="-$last";
	undef $last;
      }

      $txt.="," if length $txt;
      $txt.=$n;
    }
    else
    # contiguous - note this value as uncommited
    { $last=$n;
    }

    $next=$n+1;
  }

  if (defined $last)
  { $txt.="-$last";
  }

  return $txt;
}

=item LoadIndex(I<indexfile>)

Add the contends of the specified index file
to this index.

=cut

sub LoadIndex
{ my($this,$ndxfile)=@_;

  if (! cs::Misc::openr(cs::WordIndex::INDEX,$ndxfile))
  { warn "$::warningContext: can't read from $ndxfile: $!\n";
    return;
  }

  my $ndx = $this->{NDX};

  my($word);
  local($_);

  NDXLINE:
  while (defined($_=<INDEX>))
  {
    { local($::warningContext)="$::cmd: $ndxfile:$.";

      chomp;

      /^(\S+)\s*/ || next NDXLINE;
      $word=$1; $_=$';

      # don't decode lines unless they must be merged with existing data
      if (! exists $ndx->{$word})
      { $ndx->{$word}=$_;
	next NDXLINE;
      }

      my $wr = $this->_WordIndex($word);

      my $r = decodeIndexLine($_);
      WORDFILE:
      for my $file (keys %$r)
      { if (! exists $wr->{$file})
	{ $wr->{$file}=$r->{$file};
	  next WORDFILE;
	}

	for my $i (@{$r->{$file}})
	{ _addToWordIndex($wr,$file,$i);
	}
      }
    }
  }

  close(INDEX);
}

sub _decodeIndexLine
{ local($_)=@_;

  my $r = {};

  my $file;
  my $low;
  my $high;

  FIELD:
  for my $field (split(/\s+/))
  { if ($field !~  m:(.*)/:)
    { warn "$::warningContext: bad field \"$field\"\n\tline is \"$_\"\n";
      next FIELD;
    }

    $file=$1; $field=$';
    $r->{$file}=[] if ! exists $r->{$file};

    NUMRANGE:
    for my $numrange (split(/,/,$field))
    { if ($numrange =~ /^\d+$/)
      { $low=$high=$numrange;
      }
      elsif ($numrange =~ /^(\d+)-(\d+)$/)
      { $low=$1; $high=$2;
      }
      else
      { warn "$::warningContext: bad line range \"$numrange\"\n";
	next NUMRANGE;
      }

      for my $i ($low..$high)
      { push(@{$r->{$file}},$i);
      }
    }
  }

  return $r;
}

=item RemoveFile(I<$filename>)

Remove all mention of the specified I<filename> from the index.

=cut

sub RemoveFile($$)
{ my($this,$file)=@_;

  for my $word (keys %{$this->{NDX}})
  { my $wr = $this->_WordIndex($word);
    delete $wr->{$file} if exists $wr->{$file};
  }
}

=item SearchRE(I<regexp>)

Return the Word indices for all words matching the supplied I<regexp>
as a hashref mapping words to word indices as returned by the B<WordIndex> method.

=cut

sub SearchRE
{ my($this,$re)=@_;

  my $hits = {};

  for my $word ( grep(/$re/, keys %{$this->{NDX}} ) )
  { $hits->{$word}=$this->WordIndex($word,1);
  }

  return $hits;
}

=item CollateResults(I<resulthash>,I<searchresult>...)

This method is intended as an aid to rating results
or reporting results.

For each search result supplied
(in the form of a hashref mapping word to word index as from the B<SearchRE> method),
populate the supplied I<resulthash>
with a map of filename to linemaps
where the linemaps are a hashref mapping line number to an arrayref of words on the line.

=cut

sub CollateResults
{ my($this,$fmap)=(shift,shift);

  my $wr;
  my $lmap;

  for my $sr (@_)
  {
    for my $word (keys %$sr)
    { $wr = $sr->{$word};
      for my $file (keys %$wr)
      { $fmap->{$file}={} if ! exists $fmap->{$file};
	$lmap=$fmap->{$file};
	for my $lineno (@{$wr->{$file}})
	{ $lmap->{$lineno}=[] if ! exists $lmap->{$lineno};
	  push(@{$lmap->{$lineno}}, $word);
	}
      }
    }
  }
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt> 24apr2002

=cut

1;
