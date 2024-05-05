#!/bin/sh
#
# Additional environment actions for the css project.
# - Cameron Simpson <cs@cskk.id.au>
#

export VIRTUAL_ENV=$ENV_DEV_DIR/venv    # for uv
export PYTHON3=$VIRTUAL_ENV/bin/python3
export PATH=$ENV_DEV_DIR/bin-cs:$ENV_DEV_DIR/bin:$VIRTUAL_ENV/bin:$PATH
##export PYTHONWARNINGS=default
