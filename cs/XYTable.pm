#!/usr/bin/perl
#
# Code to two dimensional tables.
# Row zero is the description, with headings, field keys etc.
# Each element has:
#	COLKEYS	Array:	Fieldnames for each column.
#	MARKUP	Hash:	field => { HTML => this->Method(column#,row#,$datum) -> @html
#				 }
#	TITLE	Scalar:	column title
#	STATE	Hash:	{ various things needed by markup methods etc }
#	ROWS	Array:	Array of arrays of data.
# Each element of the subsequent rows is the data for the field.
#	DATA	The data for this field.
#	
# - Cameron Simpson <cs@zip.com.au> 11jun97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::XYTable;

sub new;	# fwd decl

@cs::XYTable::ISA=();

undef %cs::XYTable::_DBs;

sub new
{ my($class,$state,@cols)=@_;

  my($this)={ COLNDX	=> {},
	      COLKEYS	=> [],
	      ROWNDX	=> {},
	      ROWKEYS	=> [],
	      TITLES	=> [],
	      MARKUP	=> {},
	      ROWS	=> [],
	      DEFAULT	=> 0,
	      STATE	=> $state,
	    };

  bless $this, $class;

  for my $colkey (@cols)
	{ $this->_MkColKey($colkey);
	}

  $this;
}

sub _MkColKey
{ my($this,$colkey)=@_;
  
  my $colndx = $this->{COLNDX};
  die "$this: multiple _MkColKey of \"$colkey\""
	if exists $colndx->{$colkey};

  my $keys = $this->{COLKEYS};
  push(@$keys,$colkey);
  $colndx->{$colkey}=$#$keys;
}

sub _MkRowKey
{ my($this,$rowkey)=@_;
  
  my $rowndx = $this->{ROWNDX};
  die "$this: multiple _MkRowKey of \"$rowkey\""
	if exists $rowndx->{$rowkey};

  my $keys = $this->{ROWKEYS};
  push(@$keys,$rowkey);
  $rowndx->{$rowkey}=$#$keys;
}

sub Default($;$)
{ my($this,$dflt)=@_;

  return $this->{DEFAULT} if ! defined $dflt;

  $this->{DEFAULT}=$dflt;
}

sub ColTitle($$;$)
{ my($this,$colkey,$title)=@_;

  my($colnum)=$this->ColIndex($colkey);
  my($titles)=$this->{TITLES};

  if (defined $title)
	{ $titles->[$colnum]=$title;
	  return;
	}

  return $colkey if ! defined $titles->[$colnum];

  $title = $titles->[$colnum];
  ref($title) && ::reftype($title) eq CODE
	? $title->($this,$colkey)
	: $title;
}

sub ColIndex($$)
{ my($this,$colkey)=@_;

  my $colndx = $this->{COLNDX};

  if (! exists $colndx->{$colkey})
  { $this->_MkColKey($colkey);
  }

  return $colndx->{$colkey};
}

sub RowIndex($$)
{ my($this,$rowkey)=@_;

  my $rowndx = $this->{ROWNDX};

  if (! exists $rowndx->{$rowkey})
	{ $this->_MkRowKey($rowkey);
	}

  return $rowndx->{$rowkey};
}

sub ColKeys($)
{ @{shift->{COLKEYS}};
}

sub RowKeys($)
{ @{shift->{ROWKEYS}};
}

sub Rows($)
{ @{shift->{ROWS}}
}

sub Row($$)
{ my($this,$rowkey)=@_;

  my $rownum = $this->RowIndex($rowkey);
  my $rows   = $this->{ROWS};

  $rows->[$rownum]=[]
	if $rownum > $#$rows || ! defined $rows->[$rownum];

  $rows->[$rownum];
}

sub Datum($$$;$)
{ my($this,$colkey,$rowkey,$method)=@_;

  my($col)=$this->ColIndex($colkey);
  my($row)=$this->Row($rowkey);

  if ($col > $#$row || ! defined $row->[$col])
  { my $dflt = $this->{DEFAULT};

    ## warn "create $colkey/$rowkey";
    $row->[$col]=( ref($dflt) && ::reftype($dflt) eq CODE
		   ? &$dflt()
		   : $dflt
		 );
  }

  return $row->[$col] if ! defined $method;

  ## warn "run METHOD($method) on $colkey/$rowkey";

  &$method($this,$col,$rowkey,$row->[$col]);
}

sub Store
{ my($this,$colkey,$rowkey,$datum)=@_;
  ## warn "Store($colkey,$rowkey,$datum)";

  my($col)=$this->ColIndex($colkey);
  my($row)=$this->Row($rowkey);

  $row->[$col]=$datum;
}

1;
