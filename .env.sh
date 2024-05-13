#!/bin/sh
#
# Additional environment actions for the css project.
# - Cameron Simpson <cs@cskk.id.au>
#

for venv_sfx in "-$ARCH" ""
do
  venv=$ENV_DEV_DIR/venv$venv_sfx
  [ -d "$venv" ] && [ -x "$venv/bin/python3" ] && break
  venv=
done
[ -n "$venv" ] && export PYTHON3=$ENV_DEV_DIR/venv/bin/python3
export PATH=$ENV_DEV_DIR/bin-cs:$ENV_DEV_DIR/bin:$ENV_DEV_DIR/venv/bin:$PATH
##export PYTHONWARNINGS=default
