#!/usr/bin/perl
#
# Methods for making forms.
#	- Cameron Simpson <cs@zip.com.au> 07aug97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HTML;

package cs::HTML::Form;

@cs::HTML::Form::ISA=qw();

sub new($$;$)
{
  my($class,$action,$method)=@_;
  $method=GET if ! defined $method;	# friendlier to bookmarks

  my($F)=bless { HTML => [],
		 STACKED_HTML => [],
		 DEFAULTS => {},
		 VALUES => {},
		 FLAGS => {},
		 PARAMS => { METHOD => $method,
			     ACTION => $action,
			   },
	       }, $class;

  if ($method eq POST)
	{ ## $F->{PARAMS}->{ENCTYPE}="application/x-www-form-urlencoded";
	}

  $F;
}

# close form and return HTML (a single FORM tag with content)
sub Close
{ my($F)=shift;
  my($Q)=$F->{Q};
  my($_Q)=$Q->{_CGI};
  my($dflt)=$F->Defaults();
  my($v);

  # unroll stack
  while (@{$F->{STACKED_HTML}})
	{ $F->MarkUp(@{$F->StackedMarkUp()});
	}

  for (sort keys %$dflt)
	{ if (! defined ($v=$F->Value($_)))
		{ $F->Hidden($_,$dflt->{$_});
		}
	}

  if (! $F->_Flag(HAS_SUBMIT))
	{ $F->Submit(SUBMIT);
	}

  [FORM,$F->{PARAMS},@{$F->{HTML}}];
}

sub StackMarkUp
{
  my($F)=@_;
  push(@{$F->{STACKED_HTML}},$F->{HTML});
  $F->{HTML}=[];
}
# pop and return HTML at top of stack
sub StackedMarkUp
{
  my($F)=@_;
  my($html)=$F->{HTML};

  $F->{HTML}=( @{$F->{STACKED_HTML}}
		? pop(@{$F->{STACKED_HTML}})
		: []
	     );

  $html;
}

sub _Value
{ my($F,$field,$v)=@_;
  $field=uc($field);
  $v=1 if ! defined $v;
  $F->{VALUES}->{$field}=$v;
}

sub _Flag
{ my($F,$flag,$v)=@_;
  
  if (! defined $v)
	{ return defined $F->{FLAGS}->{$flag}
	      && $F->{FLAGS}->{$flag};
	}

  $F->{FLAGS}->{$flag}=$v;
}

sub Defaults
{ my($F)=shift;
  $F->{DEFAULTS};
}

# set or check defaults
# Default({ field => value, ...})
# Default(field,value)
# Default(field)
sub Default
{ my($F,$dflt,$v)=@_;

  if (ref $dflt)
	{ for (keys %$dflt)
	      { $F->Default($_,$dflt->{$_});
	      }
	}
  else
  { $dflt=uc($dflt);

    if (defined $v)
	{ $F->{DEFAULTS}->{$dflt}=$v;
	}
    else{ return undef if ! defined $F->{DEFAULTS}->{$dflt};
	  return $F->{DEFAULTS}->{$dflt};
	}
  }
}

# add some markup
sub MarkUp
{ my($F)=shift;
  push(@{$F->{HTML}},@_);
}

# add a submit button
sub Submit{ my($F)=shift; $F->MarkUp($F->SubmitMU(@_)); }
# submit button markup
sub SubmitMU
{ my($F,$field,$label)=@_;
  $label=$F->Default($field) if ! defined $label;
  $label='Submit' if ! defined $label;

  $F->_Value($field,$label);
  $F->_Flag(HAS_SUBMIT,1);

  [INPUT,{ TYPE => "submit",
		      NAME => $field,
		      VALUE=> $label }];
}

# add a text field
sub TextField{ my($F)=shift; $F->MarkUp($F->TextFieldMU(@_)); }
sub TextFieldMU
{ my($F,$field,$v,$size,$maxsize)=@_;
  $v=$F->Default($field) if ! defined $v;
  $size=::max(16,2*length($v)) if ! defined $size;

  my($attrs);

  $F->_Value($field,$v);
  $attrs={ TYPE	=> "text",
	   NAME	=> uc($field),
	   VALUE=> $v,
	   SIZE	=> $size,
	 };
  $attrs->{MAXLENGTH}=$maxsize if defined $maxsize;

  [INPUT,$attrs];
}

# add a text area
sub TextArea{ my($F)=shift; $F->MarkUp($F->TextAreaMU(@_)); }
sub TextAreaMU
	{ my($F,$field,$v,$rows,$cols)=@_;
	  $v=$F->Default($field) if ! defined $v;

	  my(@lines);
	  @lines=split(/\n/,$v) if ! defined $rows || ! defined $cols;

	  $rows=::max(4,scalar(@lines)) if ! defined $rows;
	  $cols=::max(16,map(length,@lines)) if ! defined $cols;

	  $F->_Value($field,$v);
	  [TEXTAREA,{ NAME => uc($field),
		      ## TYPE => "textarea",
		      ROWS => $rows,
		      COLS => $cols }, $v];
	}

# add a password field
sub Password{ my($F)=shift; $F->MarkUp($F->PasswordMU(@_)); }
sub PasswordMU
	{ my($F,$field,$v,$size,$maxsize)=@_;
	  $v=$F->Default($field) if ! defined $v;
	  $size=::max(10,length($v)) if ! defined $size;
	  $maxsize=$size if ! defined $maxsize;

	  $F->_Value($field,$v);
	  [INPUT,{ NAME		=> uc($field),
		   TYPE		=> "password",
		   VALUE	=> $v,
		   SIZE		=> $size,
		   MAXLENGTH	=>$maxsize }];
	}

# add a hidden field
sub Hidden{ my($F)=shift; $F->MarkUp($F->HiddenMU(@_)); }
sub HiddenMU
{ my($F,$field,$v)=@_;
  $v=$F->Default($field) if ! defined $v;

  $F->_Value($field,$v);
  [INPUT,{ NAME	=> uc($field),
	   TYPE	=> "hidden",
	   VALUE=> $v }];
}

# add a popup
sub Popup{ my($F)=shift; $F->MarkUp($F->PopupMU(@_)); }
sub PopupMU
{ my($F,$field,$map,$selected,$keylist)=@_;
  $selected=$F->Default($field) if ! defined $selected;

  ## warn "PopupMU: keylist=[@$keylist]";
  $F->_Value($field,$selected);
  tagSelect($field,$map,$keylist,$selected,undef,0);
}

# add a scrolling list
sub ScrollingList{ my($F)=shift; $F->MarkUp($F->ScrollingListMU(@_)); }
sub ScrollingListMU
{ my($F,$field,$map,$selected,$keylist,$size,$multiple)=@_;
  $selected=$F->Default($field) if ! defined $selected;
  $size=0 if ! defined $size;

  $F->_Value($field,$selected);
  tagSelect($field,$map,$keylist,$selected,$size,$multiple);
}

# return markup for a tag selection
sub tagSelect($$$$$$)
{ my($field,$map,$keylist,$selected,$size,$multiple)=@_;
  $multiple=0 if ! defined $multiple;

  ## warn "selected=[".cs::Hier::h2a($selected,0)."]";
  if (! defined $selected)	{ $selected=[]; }
  elsif (! ref $selected)	{ $selected=[ $selected ]; }

  if (! defined $keylist)
  { $keylist=[ sort keys %$map ]; }
  else
  { $keylist=_completeKeyList([ @$keylist ], sort keys %$map); }

  # 0 means pick a size, undef means no size (popup)
  if (defined $size && $size == 0)
  { $size=::min(5,scalar(@$keylist));
  }

  my(%sel);
  map($sel{$_}=1,@$selected);

  $field=uc($field);

  my($args)={ NAME => $field };
  my(@content,$opt);

  if (defined $size)	{ $args->{SIZE}=$size; }
  if ($multiple)	{ $args->{MULTIPLE}=undef; }

  for (@$keylist)
  { $opt={ VALUE => $_ };
    if (defined $sel{$_})
    { $opt->{SELECTED}=undef;
    }

    push(@content,[OPTION,$opt],$map->{$_});
  }

  [SELECT, $args, @content];
}

sub _completeKeyList
{ my($keylist,@keys)=@_;
  my(%keys);
  map($keys{$_}=1,@$keylist);
  push(@$keylist,grep(! defined $keys{$_},sort @keys));
  $keylist;
}

1;
