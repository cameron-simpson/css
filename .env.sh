#!/bin/sh
#
# Additional environment actions for the css project.
# - Cameron Simpson <cs@cskk.id.au>
#

: "${VIRTUAL_ENV:=}"

[ -n "$VIRTUAL_ENV" ] || {
  for venv_sfx in "-$ARCH" ""
  do
    venv=$ENV_DEV_DIR/venv$venv_sfx
    [ -x "$venv/bin/python3" ] && break
    venv=
  done

  [ -z "$venv" ] || export VIRTUAL_ENV=$venv
}

[ -z "$VIRTUAL_ENV" ] || {
  export PYTHON3=$VIRTUAL_ENV/bin/python3
  export PATH=$ENV_DEV_DIR/bin-cs:$ENV_DEV_DIR/bin:$VIRTUAL_ENV/bin:$PATH
}

##export PYTHONWARNINGS=default
