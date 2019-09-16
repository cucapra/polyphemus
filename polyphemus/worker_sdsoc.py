import os
import glob
import json
import time
import state

from .stages_common import work, task_config, update_make_conf
from .db import CODE_DIR

# For executing on a Xilinx Zynq board.
ZYNQ_SSH_PREFIX = ['sshpass', '-p', 'root']  # Provide Zynq SSH password.
ZYNQ_HOST = 'zb1'
ZYNQ_DEST_DIR = '/mnt'
ZYNQ_REBOOT_DELAY = 40


def stage_sdsoc_make(db, config):
    """Work stage: run make command. Assumes that at the end of the make
    command, work equivalent to the stage_hls is done, i.e., either
    estimation data has been generated or a bitstream has been
    generated.
    """

    prefix = config["HLS_COMMAND_PREFIX"]

    with work(db, state.MAKE, state.MAKE_PROGRESS, state.HLS_FINISH) as task:
        task_config(task, config)

        # Simple make invocation for SDSoC.
        make = [
            'make',
            'ESTIMATE={}'.format(task['estimate']),
            'PLATFORM={}'.format(task['platform']),
            'TARGET={}'.format(config['EXECUTABLE_NAME']),
        ]

        make_cmd = prefix + make
        if task['config']['directives']:
            make_cmd.append(
                'DIRECTIVES={}'.format(task['config']['directives'])
            )

        update_make_conf(make_cmd, task, db, config)

        # Run the make target
        task.run(
            make_cmd,
            timeout=config["SYNTHESIS_TIMEOUT"],
            cwd=CODE_DIR,
        )


def stage_zynq_fpga_execute(db, config):
    """Work stage: upload bitstream to the FPGA controller, run the
    program, and output the results.

    This stage currently assumes we want to execute on a Xilinx Zynq
    board, which is accessible via SSH. We require `sshpass` to provide
    the password for the board (because the OS that comes with ZedBoards
    hard-codes the root password as root---not terribly secure, so the
    board should clearly not be on a public network).
    """
    with work(db, state.HLS_FINISH, state.RUN, state.DONE) as task:

        # Do nothing in this stage if we're just running estimation.
        if task['config'].get('estimate') or task['config'].get('skipexec'):
            task.log('skipping FPGA execution stage')
            return

        # Copy the compiled code (CPU binary + FPGA bitstream) to the
        # Zynq board.
        bin_dir = os.path.join(task.code_dir, 'sd_card')
        bin_files = [os.path.join(bin_dir, f) for f in os.listdir(bin_dir)]
        dest = '{}:{}'.format(ZYNQ_HOST, ZYNQ_DEST_DIR)
        task.run(
            ZYNQ_SSH_PREFIX + ['scp', '-r'] + bin_files + [dest],
            timeout=1200
        )

        # Restart the FPGA and wait for it to come back up.
        task.run(
            ZYNQ_SSH_PREFIX + ['ssh', ZYNQ_HOST, '/sbin/reboot'],
        )
        task.log('waiting {} seconds for reboot'.format(ZYNQ_REBOOT_DELAY))
        time.sleep(ZYNQ_REBOOT_DELAY)

        # Run the FPGA program and collect results
        task.run(
            ZYNQ_SSH_PREFIX + [
                'ssh', ZYNQ_HOST,
                'cd {}; ./{}'.format(ZYNQ_DEST_DIR,
                                     config['EXECUTABLE_NAME']),
            ],
            timeout=120
        )
