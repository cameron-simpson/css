#!/usr/bin/perl
#
# Bookmark data.
#	- Cameron Simpson <cs@zip.com.au> 27mar98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Math;
use cs::IFMSink;

package cs::Web::Bookmarks;

undef %cs::Web::Bookmarks::_URLndx;

sub new
{ my($class,$title,$attrs,$parent)=@_;
  $attrs={} if ! defined $attrs;

  my($this)={ TITLE	=> $title,
	      TITLEKEY	=> titleKey($title),
	      DESC	=> [],
	      PARAMS	=> $attrs,
	      ENTRIES	=> [],
	      SUBCATS	=> [],
	    };

  bless $this, $class;

  if (ref $parent)
  # attach to larger structure
  { $parent->_AddCat($this);
  }
  elsif (defined $parent)
  # attach to file
  { ::need(cs::Object);
    ::need(cs::Persist);
    cs::Object::reTIEHASH(0,$this,cs::Persist,$parent,1);
    blessStruct($this);
  }

  $this;
}

sub blessStruct
{ my($this,$class)=@_;
  return if ! ref $this;
  $class=cs::Web::Bookmarks if ! defined $class;

  bless $this, $class;

  { my $subcats = $this->SubCats();
    if (::reftype($subcats) ne ARRAY)
    { warn "SUBCATS not an array:\n".cs::Hier::h2a($this,1);
      $this->{SUBCATS}=[];
    }
    else
    { for my $subcat (@$subcats)
      { blessStruct($subcat,$class);
      }
    }
  }

  { my $entries = $this->Entries();
    if (::reftype($entries) ne ARRAY)
    { warn "ENTRIES not an array:\n".cs::Hier::h2a($this,1);
      $this->{ENTRIES}=[];
    }
    else
    { for my $e (@$entries)
      { blessStruct($e,$class);
      }
    }
  }
}

sub Entries
{ my($this)=shift;
  $this->{ENTRIES}=[] if ! exists $this->{ENTRIES};
  warn "ENTRIES=".cs::Hier::h2a($this->{ENTRIES},0)
	if ::reftype($this->{ENTRIES}) ne ARRAY;
  $this->{ENTRIES};
}
sub SubCats
{ my($this)=shift;
  $this->{SUBCATS}=[] if ! exists $this->{SUBCATS};
  $this->{SUBCATS};
}

sub WriteMarks
{ my($this,$file,$title,@html)=@_;
  $title=$this->Title() if ! defined $title || ! length $title;

  ::need(cs::HTML);

  my $s = (ref $file ? $file : new cs::IFMSink (PATH,$file));
  return 0 if ! defined $s;

  cs::HTML::tok2s(
	$s,
	cs::HTML::HTML($title,
			[],
			{},
			$this->Bm2html($title)));
}

sub AddCat($)
{ my($this,$cat_title)=@_;
  ## {my(@c)=caller;warn "make new category for $cat_title from [@c]";}
  ## warn "add \"$cat_title\" to ".$this->Title()."\n";
  new cs::Web::Bookmarks ($cat_title,{},$this);
  # citing "$this" in new() adds us to the parent
}
sub _AddCat
{ my($this,$cat)=@_;
  push(@{$this->SubCats()},$cat);
  $cat;
}

sub AddEntry(\%$;\%$)
{ my($this,$title,$attrs,$unique)=@_;
  $attrs={} if ! defined $attrs;
  $unique=0 if ! defined $unique;

  my $entry = new cs::Web::Bookmarks ($title,$attrs);

  if (exists $entry->{PARAMS}->{HREF})
  { _noteURL($entry->{PARAMS}->{HREF},$this,$entry);
  }

  if ($unique)
  # strip duplicates
  { my $entries = $this->Entries();
    my $ntitle = $entry->Title();
    ## warn "entries=[@$entries]";
    my @u = grep($_->Title() ne $ntitle, @$entries);

    if (@u < @$entries)
    { $this->{ENTRIES}=[ @u ];
    }
  }

  ## warn "AddEntry(title=[$title]) to ".$this->Title();
  push(@{$this->{ENTRIES}},$entry);
  ## warn "post-push: ENTRIES=[@{$this->{ENTRIES}}]";

  $entry;
}

sub _noteURL($\%\%)
{ my($url,$cat,$ent)=@_;

  my($U)=\%cs::Web::Bookmarks::_URLndx;
  if (! exists $U->{$url})
  { $U->{$url}=[];
  }

  push(@{$U->{$url}},{ CAT => $cat, ENTRY => $ent });
}

sub Find
{ my($this,$cat,$force)=@_;
  $force=0 if ! defined $force;

  ## warn "cs::Web::Bookmarks::Find(cat=$cat,force=$force)...";

  my(@catTitles)=grep(length,split(m:/+:,$cat));

  my($sofar,$title,$subcat,$subcats,@branch);

  $sofar='';

  while (@catTitles)
  {
    $title = shift(@catTitles);
    $subcat= lc($title);

    ## warn "Find: subcat=[$subcat], cattitles=[@catTitles]";

    $sofar.='/' if length $sofar;
    $sofar.=$subcat;

    $subcats=$this->SubCats();

    ## warn "grepping for [$subcat]";
    @branch=grep(lc($_->{TITLE}) eq $subcat, @$subcats);

    if (@branch == 0)
    { if ($force)
      { ## warn "add new cat: $title\n";
	@branch=$this->AddCat($title);
      }
      else
      { warn "$::cmd: no such subcategory: $sofar\n";
	return undef;
      }
    }

    if (@branch > 1)
    { warn "$::cmd: ambiguous subcategory: $sofar\n";
      warn cs::Hier::h2a([ map($_->{TITLE},@$subcats) ],1);
      return undef;
    }

    $this=$branch[0];

    bless $this, cs::Web::Bookmarks
    if ! $this->isa(cs::Web::Bookmarks);
  }

  $this;
}

sub Bm2htmlFile
{ my($this,$target)=(shift,shift);

  if (! -d "$target/.")
  {
    my $s = new cs::IFMSink (PATH,$target);
    ## warn "writing $target ...\n" if -t STDERR;
    return $this->Bm2htmlSink($s,@_);
  }

  my $mainTitle = shift;

  my $stubBm = new cs::Web::Bookmarks $mainTitle;

  my @c = caller;
  ## warn "this=$this from [@c]\n";
  ## warn "subcats=$cats from [@c]\n";

  for my $subcat (@{$this->SubCats()})
  { ## warn "subcat=$subcat from [@c]\n";
    my $subkey = $subcat->TitleKey();
    my $subtitle = $subcat->Title();
    my $subtarget = "$target/$subkey";

    if (-d "$subtarget/.")
    { $subcat->Bm2htmlFile($subtarget, "$subtitle <- $mainTitle");
      $stubBm->AddEntry("$subtitle/",{ HREF => "$subkey/index.html" });
    }
    else
    { $subcat->Bm2htmlFile("$subtarget.html", "$subtitle <- $mainTitle");
      $stubBm->AddEntry("$subtitle/",{ HREF => "$subkey.html" });
    }
  }

  for my $entry (@{$this->Entries()})
  {
    ## warn "add entry=".cs::Hier::h2a($entry,0);
    $stubBm->AddEntry($entry->{TITLE}, $entry->{PARAMS});
  }

  my $s = new cs::IFMSink (PATH,"$target/index.html");
  if (! ref $s)
  { warn "no sink for $target/index.html: $!";
  }
  else
  { ## warn "writing $target/index.html ...\n";
    $stubBm->Bm2htmlSink($s,$mainTitle);
  }
}

sub Bm2htmlSink
{ my($this,$s,$title)=(shift,shift,shift);

  my @html = $this->Bm2html($title,@_);

  ::need(cs::HTML);
  cs::HTML::tok2s(
	$s,
	cs::HTML::HTML($title, [], {}, @html));
}

sub Bm2html
{ my($this,$mainTitle)=@_;

  local($cs::Web::Bookmarks::_MainTitle)=$mainTitle;

  $this->_Bm2html(1,'','','');
}

sub Title
{ shift->{TITLE};
}

sub TitleKey
{ shift->{TITLEKEY};
}

# get short key from title for making NAME tags
sub titleKey
{ local($_)=@_;

  ## my($o)=$_;

  while (s/\([^()]*\)//)	# strip comments
	{}

  s/\s+/ /g;			# collapse whitespace
  s/ - .*//;			# strip full desc
  $_=lc;			# downcase
  s:[^ \w.']+:,:g;		# commaise
  s/^,//;			# tidy
  s/,$//;

  $_=join('',map(ucfirst,split(/ +/))); # join

  ## warn "[$o] -> [$_]\n";

  $_;
}

sub Depth
{ my($this)=@_;

  my @cats = $this->SubCats();

  return 0 if ! @cats;

  1+::max(map($_->Depth(), @cats));
}

sub _Bm2html
{ my($this,$hlev,$key,$superKey,$superTitle)=@_;

  my(@html)=();

  my($bigTitle)=$superTitle;
  $bigTitle.="/" if length $bigTitle;
  $bigTitle.=$this->{TITLE};

  my(@htitle)=[A,{NAME => $key},
		 length($this->{TITLE})
			? $this->{TITLE}
			: $cs::Web::Bookmarks::_MainTitle
	      ];

  if (length $superKey)
  { push(@htitle," ",
		 [SMALL,
		  "[ ^-",
		  [A,{HREF => "#$superKey"},
		     length($superTitle)
		      ? $superTitle
		      : "Top"],
		  " ]"]);
  }

  # H5 and up tend to rendered smaller than normal text
  # stupid but true!
  if ($hlev > 4)
  { push(@html,[P],"\n",[B,@htitle],"\n",[BR],"\n");
  }
  else
  { push(@html,["H$hlev",@htitle],"\n");
  }

  if (@{$this->{DESC}})
  { push(@html,@{$this->{DESC}},[BR],"\n");
  }

  my($subcats,$entries)=($this->SubCats(),$this->Entries());
  my(@ndx,$subcat,$subkey,$entry);


  { my @ndx = ();

    for my $i (0..$#$subcats)
    {
      $subcat=$subcats->[$i];
      $subkey=$subcat->TitleKey();
      push(@ndx,[A,{HREF => "#${key}/$subkey"},$subcat->{TITLE}]);
    }

    if (::reftype($entries) ne ARRAY)
    { warn "entries=$entries";
      $entries=[];
    }

    if (@$entries ? @ndx : (@ndx > 1))
    { my $first = 1;

      for my $ndx (@ndx)
      { if ($first)
	{ push(@html,"[ ");
	  $first=0;
	}
	elsif (@ndx > 3)
	{ push(@html,[BR],"\n| ");
	}
	else
	{ push(@html," | ");
	}
	push(@html,$ndx);
      }

      push(@html," ]",[P],"\n");
    }
  }

  ## warn "entries=[$entries]";
  if (@$entries)
  { my @li = ();

    for my $e (@$entries)
    { my $desc = $e->{DESC};
      if (! ref $desc)
      { warn "DESC=\"$desc\"";
	$e->{DESC}=(length($desc) ? [$desc] : []);
      }
      elsif (::reftype($desc) ne ARRAY)
      { warn "DESC=".cs::Hier::h2a($desc,0);
	$e->{DESC}=[];
      }

      ## warn "e->{DESC} is ".cs::Hier::h2a($e->{DESC});
      push(@li,[LI,
		( exists $e->{PARAMS}->{HREF}
		  ? [A,{HREF => $e->{PARAMS}->{HREF}},
		      $e->{TITLE}]
		  : $e->{TITLE}
		),
		( @{$e->{DESC}}
		  ? ([BR],"\n",@{$e->{DESC}},"\n")
		  : ()
		),
	       ]);
    }

    push(@html, [UL, @li], "\n");
  }

  for my $i (0..$#$subcats)
  { $subcat=$subcats->[$i];
    $subkey=$subcat->TitleKey();
    push(@html,$subcat->_Bm2html($hlev+1,"${key}/$subkey",$key,$bigTitle));
  }

  @html;
}

sub Prune
{ my($this,$cat)=@_;

  my(@cat)=grep(length,split(m:/+:,lc($cat)));
  if (! @cat)
  { warn "$::cmd: skipping prune of empty category\n";
    return;
  }

  my($sofar,$subcats,@branch);

  $cat=pop(@cat);
  $sofar=join('/',@cat);
  if (@cat)
  { return if ! defined ($this=$this->Find($sofar));
  }

  $sofar.='/' if length $sofar;
  $sofar.=$cat;

  $subcats=$this->SubCats();

  @branch=grep(lc($_->{TITLE}) eq $cat, @$subcats);

  if (@branch == 0)
  { warn "$::cmd: no such subcategory: $sofar\n";
    warn "\tcan't prune: $cat\n";
    return;
  }

  if (@branch > 1)
  { warn "$::cmd: ambiguous subcategory: $sofar\n";
    warn "\tcan't prune: $cat\n";
    warn cs::Hier::h2a([ map($_->{TITLE},@$subcats) ],1);
    return;
  }

  $this->{SUBCATS}=[ grep(lc($_->{TITLE}) ne $cat,
			@$subcats) ];
}

sub Walk
{ my($this,$fn,@args)=@_;
  _Walk($this,[],$fn,[@args]);
}

sub _Walk
{ my($this,$sofar,$fn,$argv)=@_;

  &$fn($this,CAT,$sofar,@$argv);

  for my $entry (@{$this->Entries()})
  { &$fn($this,ENTRY,$sofar,$entry,@$argv);
  }

  my($further)=[ @$sofar, $this ];

  for my $subcat (@{$this->SubCats()})
  { _Walk($subcat,$further,$fn,$argv);
  }
}

sub Bm2ring
{ my($this,$sink)=@_;
  Walk($this,\&_Bm2ring,$sink);
}
sub _Bm2ring
{ my($this,$type,$ancestors)=(shift,shift,shift);

  if ($type eq CAT)
  { my($sink)=shift;
    $sink->Put( join(':',
		     grep(length,
			  map($_->Title(),@$ancestors))), "\n");
    $sink->Put( "\t",
	      join('/',map($_->TitleKey(),
			   @$ancestors)),
	      "\n");
    $sink->Put( "\t",
	      "[", join("\n\t ",
			map($_->Title(),
			    @{$this->SubCats()})),
	      "\n\t]\n");
  }
  elsif ($type eq ENTRY)
  { my($entry,$sink)=(shift,shift);
    $sink->Put( join(':',
		     grep(length,
			  map($_->Title(),@$ancestors),
			  $entry->{TITLE})), "\n");
    $sink->Put( "\t", $entry->{PARAMS}->{HREF}, "\n");
  }
  else
  { warn "$::cmd: can't handle type \"$type\"";
  }
}

1;
