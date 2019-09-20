import threading
import os
import time
import json
import glob
import re

from .stages_common import work, task_config, update_make_conf
from . import state, worker_f1, worker_sdsoc, worker_common
from .db import CODE_DIR

# Strings corresponding to stages known to workers.
KNOWN_STAGES = {
    **worker_common.STAGE_NAMES,
    **worker_f1.STAGE_NAMES,
    **worker_sdsoc.STAGE_NAMES,
}


class WorkThread(threading.Thread):
    """A base class for all our worker threads, which run indefinitely
    to process tasks in an appropriate state.

    The thread takes the database and configuration dictionaries as well
    as a function to which these will be passed. When the thread runs,
    the function is invoked repeatedly, indefinitely.
    """

    def __init__(self, db, config, func):
        self.db = db
        self.config = config
        self.func = func
        super(WorkThread, self).__init__(daemon=True)

    def run(self):
        while True:
            self.func(self.db, self.config)


def default_work_stages(config):
    """List of strings representing stages for the configured toolchain.
    """

    # Toolchain dependent stage configuration
    stage_make = 'make_f1' if config['TOOLCHAIN'] == 'f1' else 'make_sdsoc'
    stages = ['unpack', stage_make]

    if config['TOOLCHAIN'] == 'f1':
        stages += 'afi', 'exec_f1'
    else:
        stages += ['exec_zynq']

    stages += [stage_make for i in range(config['PARALLELISM_MAKE'] - 1)]

    return stages


def work_threads(stages, config, db):
    """Return a list of (unstarted) Thread objects from a list of stage functions
    """
    return [WorkThread(db, config, stage) for stage in stages]
