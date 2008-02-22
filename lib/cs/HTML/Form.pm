#!/usr/bin/perl
#
# Methods for making forms.
#	- Cameron Simpson <cs@zip.com.au> 07aug1997
#

=head1 NAME

cs::HTML::Form - support for parsing and generating HTML markup

=head1 SYNOPSIS

use cs::HTML;

=head1 DESCRIPTION

This module supplies methods constructing HTML forms.
It is usually used in CGI scripts
via the B<Form()> method from the cs::CGI(3) module.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::HTML;

package cs::HTML::Form;

@cs::HTML::Form::ISA=qw();

=head1 OBJECT CREATION

=over 4

=item new cs::HTML::Form(I<action>, I<method>, I<enctype>)

Create a new B<cs::HTML::Form> object.
I<method> is optional and defaults to B<GET>.
I<enctype> is optional and defaults to B<application/x-www-form-urlencoded>.

=cut

sub new($$;$$)
{
  my($class,$action,$method,$enctype)=@_;
  $action=$action->ScriptURL() if ref $action;
  $method=GET if ! defined $method;	# friendlier to bookmarks
  $enctype='application/x-www-form-urlencoded' if ! defined $enctype;

  my($F)=bless { HTML => [],
		 STACKED_HTML => [],
		 DEFAULTS => {},
		 VALUES => {},
		 FLAGS => {},
		 PARAMS => { METHOD => $method,
			     ACTION => $action,
			     ENCTYPE => $enctype,
			   },
	       }, $class;

  $F;
}

=back

=head1 OBJECT METHODS

=over 4

=item Close

This signifies that you're done with the form.
The return value is a single B<FORM> tag
containing the entire HTML for the form you've constructed
in the same tokenised structure used by the cs::HTML(3) and cs::CGI(3) modules:

	[ FORM, { I<attributes...> }, I<tokens...> ]

=cut

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

=item StackMarkUp()

Save the current markup state and commence a new, empty state.
This is mostly for constructing things like TABLEs:

	# save state
	$F->StackMarkUp();
	# build a bunch of rows
	for my $n (1..4)
	{ $F->MarkUp([TR, [TD, "Label"], [TD, $F->TextFieldMU(...)]]);
	}
	# pop the rows into a table and add to the old state,
	# which is now current again
	$F->MarkUp([TABLE, $F->StackedMarkUp()]);

=cut

sub StackMarkUp
{
  my($F)=@_;
  push(@{$F->{STACKED_HTML}},$F->{HTML});
  $F->{HTML}=[];
}

=item StackedMarkUp()

Return the current markup state in an arrayref,
and pop the state stack
so the last state pushed is now again the current state.

=cut

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

=item Defaults()

Return the hashref containing the default field values for the form.

=cut

sub Defaults
{ my($F)=shift;
  $F->{DEFAULTS};
}

=item Default(I<field>, I<value>)

Set the default I<value> for the specified I<field>.
If I<value> is omitted,
return the current default or B<undef>.

=cut

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
    else
    { return undef if ! defined $F->{DEFAULTS}->{$dflt};
      return $F->{DEFAULTS}->{$dflt};
    }
  }
}

=item MarkUp(I<tokens>)

Append the specified HTML I<tokens> to the form's HTML.
The tokens are in the usual structured form.

=cut

# add some markup
sub MarkUp
{ my($F)=shift;
  push(@{$F->{HTML}},@_);
}

=item Submit(I<field>,I<label>)

Add a submit button named I<field> with value I<label> to the form.

=cut

sub Submit
{ my($F)=shift; $F->MarkUp($F->SubmitMU(@_));
}

=item SubmitMU(I<field>,I<label>)

Return an B<INPUT> token for a submit button named I<field>
with value I<label>.

=cut

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

=item TextField(I<field>,I<value>,I<size>,I<maxsize>)

Add a B<TEXT> B<INPUT> token
for a text field input
(single line)
to the form,
with initial content I<value>,
length I<size> and maximum length I<maxsize>.
If I<value> is omitted, the default value for the field is used.
If I<size> is omitted, the maximum of B<16> and twice the length of the initial value is used.
If I<maxsize> is omitted then the field has no maximum length.

=cut

# add a text field
sub TextField{ my($F)=shift; $F->MarkUp($F->TextFieldMU(@_)); }

=item TextFieldMU(I<field>,I<value>,I<size>,I<maxsize>)

Return a B<TEXT> B<INPUT> token
for a text field input
(single line)
with initial content I<value>,
length I<size> and maximum length I<maxsize>.
If I<value> is omitted, the default value for the field is used.
If I<size> is omitted, the maximum of B<16> and twice the length of the initial value is used.
If I<maxsize> is omitted then the field has no maximum length.

=cut

sub TextFieldMU
{ my($F)=shift;
  $F->_TextFieldMU(TEXT,@_);
}

=item TextArea(I<field>,I<value>,I<rows>,I<cols>)

Add a B<TEXTAREA> B<INPUT> token
(multiline)
to the form
with initial content I<value>,
I<rows> lines of input
and I<cols> columns in width.
If I<value> is omitted, the default value for the field is used.
If I<rows> is omitted, the maximum of B<4> and the number of lines in the initial value is used.
If I<cols> is omitted, the maximum of B<16> and the widest line in the initial value is used.

=cut

# add a text area
sub TextArea{ my($F)=shift; $F->MarkUp($F->TextAreaMU(@_)); }

=item TextAreaMU(I<field>,I<value>,I<rows>,I<cols>)

Return a B<TEXTAREA> B<INPUT> token
(multiline)
with initial content I<value>,
I<rows> lines of input
and I<cols> columns in width.
If I<value> is omitted, the default value for the field is used.
If I<rows> is omitted, the maximum of B<4> and the number of lines in the initial value is used.
If I<cols> is omitted, the maximum of B<16> and the widest line in the initial value is used.

=cut

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

=item Password(I<field>,I<value>,I<size>,I<maxsize>)

Add a B<PASSWORD> B<INPUT> token
for a text field input
(single line, content obscured)
to the form
with initial content I<value>,
length I<size> and maximum length I<maxsize>.
If I<value> is omitted, the default value for the field is used.
If I<size> is omitted, the maximum of B<16> and twice the length of the initial value is used.
If I<maxsize> is omitted then the field has no maximum length.

=cut

# add a password field
sub Password{ my($F)=shift; $F->MarkUp($F->PasswordMU(@_)); }

=item Password(I<field>,I<value>,I<size>,I<maxsize>)

Return a B<PASSWORD> B<INPUT> token
for a text field input
(single line, content obscured)
with initial content I<value>,
length I<size> and maximum length I<maxsize>.
If I<value> is omitted, the default value for the field is used.
If I<size> is omitted, the maximum of B<16> and twice the length of the initial value is used.
If I<maxsize> is omitted then the field has no maximum length.

=cut

sub PasswordMU
{ my($F)=shift;
  $F->_TextFieldMU(PASSWORD,@_);
}

sub _TextFieldMU
{ my($F,$type,$field,$v,$size,$maxsize)=@_;
  $v=$F->Default($field) if ! defined $v;
  $size=::max(16,2*length($v)) if ! defined $size;

  my($attrs);

  $F->_Value($field,$v);
  $attrs={ TYPE	=> TEXT,
	   NAME	=> uc($field),
	   VALUE=> $v,
	   SIZE	=> $size,
	 };
  $attrs->{MAXLENGTH}=$maxsize if defined $maxsize;

  [INPUT,$attrs];
}

=item Hidden(I<field>,I<value>)

Add a hidden field named I<field>
with value I<value>
to the form.

=cut

# add a hidden field
sub Hidden{ my($F)=shift; $F->MarkUp($F->HiddenMU(@_)); }

=item HiddenMU(I<field>,I<value>)

Return a hidden field token named I<field>
with value I<value>.

=cut

sub HiddenMU
{ my($F,$field,$v)=@_;
  $v=$F->Default($field) if ! defined $v;

  $F->_Value($field,$v);
  [INPUT,{ NAME	=> uc($field),
	   TYPE	=> "hidden",
	   VALUE=> $v }];
}

=item Popup(I<field>,I<map>,I<selected>,I<keylist>)

Add a popup B<SELECT> token
for the specified I<field>
to the form
to choose a single item amongst the specified values
(the keys of the hashref I<map>,
with descriptive strings being the values of the hashref I<map>).
If supplied,
the initial value I<selected> is preselected.
If supplied,
the arrayref I<keylist> specifies a key ordering for the popup;
it need not be a complete list of the keys in I<map>.

=cut

# add a popup
sub Popup{ my($F)=shift; $F->MarkUp($F->PopupMU(@_)); }

=item PopupMU(I<field>,I<map>,I<selected>,I<keylist>)

Return a popup B<SELECT> token
for the specified I<field>
to choose a single item amongst the specified values
(the keys of the hashref I<map>,
with descriptive strings being the values of the hashref I<map>).
If supplied,
the initial value I<selected> is preselected.
If supplied,
the arrayref I<keylist> specifies a key ordering for the popup;
it need not be a complete list of the keys in I<map>.

=cut

sub PopupMU
{ my($F,$field,$map,$selected,$keylist)=@_;
  $selected=$F->Default($field) if ! defined $selected;

  ## warn "PopupMU: keylist=[@$keylist]";
  $F->_Value($field,$selected);
  _tagSelect($field,$map,$keylist,$selected,undef,0);
}

=item ScrollingList(I<field>,I<map>,I<selected>,I<keylist>,I<size>,I<multiple>)

Add a multiple value B<SELECT> token
for the specified I<field>
to the form
to choose a single item amongst the specified values
(the keys of the hashref I<map>,
with descriptive strings being the values of the hashref I<map>).
If supplied,
the initial values in the arrayref I<selected> are preselected.
If supplied,
the arrayref I<keylist> specifies a key ordering for the popup;
it need not be a complete list of the keys in I<map>.
If supplied, I<size> specifies the number of options visible in the list at any one time;
if omitted it is the minimum of the number of keys and the value B<5>.
If supplied, the I<multiple> parameter
specifies whether multiple items may be chosen from the list;
it defaults to B<false>.

=cut

# add a scrolling list
sub ScrollingList{ my($F)=shift; $F->MarkUp($F->ScrollingListMU(@_)); }

=item ScrollingListMU(I<field>,I<map>,I<selected>,I<keylist>,I<size>,I<multiple>)

Return a multiple value B<SELECT> token
for the specified I<field>
to choose a single item amongst the specified values
(the keys of the hashref I<map>,
with descriptive strings being the values of the hashref I<map>).
If supplied,
the initial values in the arrayref I<selected> are preselected.
If supplied,
the arrayref I<keylist> specifies a key ordering for the popup;
it need not be a complete list of the keys in I<map>.
If supplied, I<size> specifies the number of options visible in the list at any one time;
if omitted it is the minimum of the number of keys and the value B<5>.
If supplied, the I<multiple> parameter
specifies whether multiple items may be chosen from the list;
it defaults to B<false>.

=cut

sub ScrollingListMU
{ my($F,$field,$map,$selected,$keylist,$size,$multiple)=@_;
  $selected=$F->Default($field) if ! defined $selected;
  $size=0 if ! defined $size;
  $multiple=0 if ! defined $multiple;

  $F->_Value($field,$selected);
  _tagSelect($field,$map,$keylist,$selected,$size,$multiple);
}

# return markup for a tag selection
sub _tagSelect($$$$$$)
{ my($field,$map,$keylist,$selected,$size,$multiple)=@_;
  $multiple=0 if ! defined $multiple;
  $keylist=[] if ! defined $keylist;

  ## warn "selected=[".cs::Hier::h2a($selected,0)."]";
  if (! defined $selected)	{ $selected=[]; }
  elsif (! ref $selected)	{ $selected=[ $selected ]; }

  ## warn "_tagSelect: orig keylist=[@$keylist]";
  $keylist=::uniq(@$keylist, sort keys %$map);
  ## warn "_tagSelect: new keylist=[@$keylist]";

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

=item FileUpload(I<field>)

Add a file upload B<INPUT> token
for the specified I<field>.

=cut

sub FileUpload
{ my($F)=shift;

  $F->MarkUp($F->FileUploadMU(@_));
}

=item FileUploadMU(I<field>)

Return a file upload B<INPUT> token
for the specified I<field>.

=cut

sub FileUploadMU($$)
{ my($F,$field)=@_;

  # coerce ENCTYPE in case the user forgot
  $F->{PARAMS}->{ENCTYPE}='multipart/form-data';

  return [INPUT, {TYPE => FILE, NAME => $field}];
}

=back

=head1 SEE ALSO

cs::HTML(3),
cs::CGI(3),

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
