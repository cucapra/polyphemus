import os

from . import state
from .stages_common import work
from .db import ARCHIVE_NAME


def stage_unpack(db, _):
    """Work stage: unpack source code.
    """
    with work('unpack', db, state.UPLOAD, state.UNPACK, state.MAKE) as task:
        # Unzip the archive into the code directory.
        os.mkdir(task.code_dir)
        task.run(["unzip", "-d", task.code_dir, "{}.zip".format(ARCHIVE_NAME)])

        # Check for single-directory zip files: if the code directory
        # only contains one subdirectory now, "collapse" it.
        code_contents = os.listdir(task.code_dir)
        if len(code_contents) == 1:
            path = os.path.join(task.code_dir, code_contents[0])
            if os.path.isdir(path):
                for fn in os.listdir(path):
                    os.rename(os.path.join(path, fn),
                              os.path.join(task.code_dir, fn))
                task.log('collapsed directory {}'.format(code_contents[0]))


STAGE_NAMES = {
    'unpack': stage_unpack,
}

