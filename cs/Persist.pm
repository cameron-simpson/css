#!/usr/bin/perl
#
# Save/restore a hash in the filesystems.
#	- Cameron Simpson <cs@zip.com.au> 11jun97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HASH;
use cs::Persist::File;
use cs::Persist::Dir;

package cs::Persist;

undef %cs::Persist::_Reg;
$cs::Persist::_DidFinish=0;

@cs::Persist::DfltFlags=();

sub END
{ finish(@_) }

sub finish
{
  ## warn "Persist::finish(@_)\n";

  my(@k)=(@_ ? @_ : keys %cs::Persist::_Reg);

  ## warn "\tk=[@k]\n";

  $cs::Persist::_DidFinish=1;

  SYNC:
  for (@k)
  {
    next SYNC if ! exists $cs::Persist::_Reg{$_};

    my $o = $cs::Persist::_Reg{$_};
    delete $cs::Persist::_Reg{$_};

##  warn "finish: sync($o->{FNAME})..."
##  if $o->{FNAME} =~ /toc\.db/;

    $o->Sync();
    $o->SetReadWrite(0);
    ## OLD WAY: delete $cs::Persist::_DB{$_};
    ## warn "unreffed [$_]\n";
  }

  ## my(@c)=caller;
  ## @k=keys %cs::Persist::_DB;
  ## warn "unfinished keys=[@k] from [@c]" if @k;
}

undef %cs::Persist::_DB;
sub db
{ my($path,$rw,$minDepth,$noCache)=@_;
  $rw=0 if ! defined $rw || ! length $rw;
  $noCache=0 if ! defined $noCache;

  ## my(@c)=caller;
  ## warn "db(path=$path,rw=$rw), caller=[@c]";

  if ($noCache || ! exists $cs::Persist::_DB{$path})
  { $cs::Persist::_DB{$path}={};

    tie(%{$cs::Persist::_DB{$path}}, cs::Persist, $path, $rw, caller)
	  || die "can't tie to $path, possible error: $!";

    warn "tied to \"$path\"" if $path =~ /toc\.db/;
  }

  my($db)=$cs::Persist::_DB{$path};
  my($obj)=tied %$db;

  if ($rw)
  { ## warn "marking $path as RW";
    $obj->{RW}=1;
  }
  else
  { ## warn "NOT marking $path as RW";
  }

  $obj->{META}->{MINDEPTH}=$minDepth if defined $minDepth;

  $cs::Persist::_DB{$path};
}

sub _reg($)
{ my($id)=@_;
  return undef if ! exists $cs::Persist::_Reg{$id};
  $cs::Persist::_Reg{$id};
}

sub _Register
{ my($this,$id)=@_;
  $cs::Persist::_Reg{$id}=$this;
}

sub _Unregister
{ my($this,$id)=@_;
  delete $cs::Persist::_Reg{$id};
}

sub TIEHASH
{
  my($class,$path,$rw,$pref,@c)=@_;
  $rw=0 if ! defined $rw;
  @c=caller if ! @c;

  my($this);

  if (stat($path)
	? -d _
	: (defined($pref) && $pref eq DIR)
     )
  {
    $this=cs::Persist::Dir::TIEHASH(cs::Persist::Dir,$path,$rw);
  }
  else
  {
    $this=cs::Persist::File::TIEHASH(cs::Persist::File,$path,$rw,@c);
  }

  $this->{PID}=$$;

  $this;
}

sub Meta { shift->{META} }
sub Schema { shift->Meta()->{SCHEMA} }

sub Fields
{ my($this,$flist)=@_;
  $flist=SUMMARY if ! defined $flist;

  ref $flist
	? @$flist
	: $flist eq ALL
	  ? $this->AllFields()
	  : $flist eq SUMMARY
	    ? $this->SummaryFields()
	    : $flist;	# huh? should whinge,
			# but we'll just return the supplied key
}

sub SummaryFields
{ my($this)=@_;
  my($meta)=$this->Meta();
  
  ## warn "called from [".join('|',caller)."]";

  exists $meta->{SUMMARY_KEYS}
	? @{$meta->{SUMMARY_KEYS}}
	: sort keys %{$this->Schema()};
}

sub AllFields
{ my($this)=@_;
  my($meta)=$this->Meta();
  
  ## warn "called from [".join('|',caller)."]";

  my(@all)=();

  if (! exists $meta->{ALL_KEYS})
  {
    my(%all)=();
    my($key,$rec);

    # compute key list
    # expensive!
    for $key ($this->KEYS())
    { 
      $rec=$this->FETCH($key);
      if (ref $rec)
	    { map($all{$_}=1,keys %$rec);
	    }
    }

    @{$meta->{ALL_KEYS}}=sort keys %all;
  }

  @{$meta->{ALL_KEYS}};
}

sub FieldDesc
{ my($this,$field)=@_;
  my($schema)=$this->Schema();

  exists $schema->{$field}
    && exists $schema->{$field}->{DESC}
	? $schema->{$field}->{DESC}
	: $field;
}

sub IsReadWrite
{ shift->TestAll(RW);
}

1;
