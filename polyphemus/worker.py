import threading
import os
import time
import json
import glob
import re

from .stages_common import work, task_config, update_make_conf
from .worker_common import stage_unpack
from . import state
from .db import CODE_DIR

from .worker_f1 import stage_f1_make, stage_afi, stage_f1_fpga_execute
from .worker_sdsoc import stage_sdsoc_make, stage_zynq_fpga_execute



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



def work_threads(db, config):
    """Get a list of (unstarted) Thread objects for processing tasks.
    """

    # Toolchain dependent stage configuration
    stage_make = stage_sdsoc_make if config['TOOLCHAIN'] == 'f1' else stage_f1_make

    STAGES = [stage_unpack, stage_make]
    if config['TOOLCHAIN'] == 'f1':
        STAGES += stage_zynq_fpga_execute
    else:
        STAGES += stage_afi, stage_f1_fpga_execute

    stages = list(STAGES) + \
        [stage_make for i in range(config['PARALLELISM_MAKE'] - 1)]
    if config['TOOLCHAIN'] == 'f1':
        stages.append(stage_afi)
    return [WorkThread(db, config, stage) for stage in stages]
