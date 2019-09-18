import argparse
import curio
import os
import sys
import time

from . import worker
from .db import JobDB
from flask.config import Config


INSTANCE_DIR = 'instance'
SOCKNAME = 'workproc.sock'
KNOWN_STAGES_STR = ', '.join(worker.KNOWN_STAGES.keys())


class WorkProc:
    """A container process for our worker threads that can receive
    notifications from a Unix domain socket.
    """

    def __init__(self, basedir, db=None):
        """Create a container using a given base directory for the
        storage and socket. Optionally, provide a database object to use
        that instead of creating a new one (to, for example, reuse its
        internal locks).
        """
        self.basedir = os.path.abspath(basedir)

        # Load the configuration. We're just reusing Flask's simple
        # configuration component here.
        self.config = Config(self.basedir)
        self.config.from_object('polyphemus.config_default')
        self.config.from_pyfile('polyphemus.cfg', silent=True)

        # Create the database.
        self.db = db or JobDB(self.basedir)

    def start(self, stages_conf=None):
        """Create and start the worker threads. If stages_conf is None, create the
        default workers for the given toolchain. If stages_confg is a list of
        strings in worker.KNOWN_STAGES then create workers mapping to those.
        """
        if stages_conf is None:
            stages = worker.default_work_stages(self.config)
        else:
            stages = [worker.KNOWN_STAGES[stage] for stage in stages_conf]

        print(stages)

        for thread in worker.work_threads(stages, self.config, self.db):
            if not thread.is_alive():
                thread.start()

    async def handle(self, client, addr):
        """Handle an incoming socket connection.
        """
        async for line in client.makefile('rb'):
            # Each line is a job name.
            job_name = line.decode('utf8').strip()
            print(job_name)

            # Just notify the database that something changed.
            with self.db.cv:
                self.db.cv.notify_all()

    def serve(self):
        """Start listening on a Unix domain socket for incoming
        messages. Run indefinitely (until the server is interrupted).
        """
        sockpath = os.path.join(self.basedir, SOCKNAME)
        if os.path.exists(sockpath):
            os.unlink(sockpath)
        try:
            curio.run(curio.unix_server, sockpath, self.handle)
        except KeyboardInterrupt:
            print ("Shutting down worker.")
            pass
        finally:
            os.unlink(sockpath)


    def poll(self):
        """Continously poll the work directory for open jobs.
        """
        try:
            while True:
                with self.db.cv:
                    self.db.cv.notify_all()

                time.sleep(2)

        except KeyboardInterrupt:
            print ("Shutting down worker.")
            pass


def notify(basedir, jobname):
    """Notify a running workproc that a new job has been added to the
    database (via the socket in basedir).
    """
    curio.run(_notify, basedir, jobname)


async def _notify(basedir, jobname):
    sockpath = os.path.join(basedir, SOCKNAME)
    line = (jobname + '\n').encode('utf8')
    sock = await curio.open_unix_connection(sockpath)
    await sock.makefile('wb').write(line)
    await sock.close()


def valid_stage(stage):
    """Check if a given string represents a valid stage
    """
    if stage not in worker.KNOWN_STAGES.keys():
        raise argparse.ArgumentTypeError("Unknown stage: %s. Valid stages are: %s" % (stage, KNOWN_STAGES_STR))

    return stage


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Start Polyphemus Work Processor.')

    # Start in polling mode instead of sockets.
    parser.add_argument('-p', '--poll',
                        action='store_true',
                        help='Poll instance directory for jobs every 2 seconds. Uses socket based communication from the server by default.')

    # Instance directory to use for managing the jobs.
    parser.add_argument('-i', '--instance-dir',
                        help='Instance directory to use for tracking jobs. Defaults to directory specified in polyphemus.cfg.',
                        type=str, action='store',
                        default=INSTANCE_DIR, dest='instance',)

    # List of stages to start this worker with.
    parser.add_argument('-s', '--stages', nargs='*',
                        help='Stages to start this WorkProc with. Defaults to ones for the current toolchain. Known stages: %s.' % KNOWN_STAGES_STR,
                        default = None, type=valid_stage)

    opts = parser.parse_args()


    p = WorkProc(opts.instance)
    p.start(opts.stages)

    if opts.poll:
        print('Starting worker in poll mode.')
        p.poll()
    else:
        print('Starting worker with socket communication.')
        p.serve()
