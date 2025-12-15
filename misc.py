
"""
    Company: Digilent RO
    Engineer: bs
    Usage: Vitis projects
    
    @Description
    Different utility functions to manage some file or
    data in a certain way that facilitates a vitis
    workspace automation.
"""
from ctypes import cdll
from logging import (Logger, INFO, StreamHandler,
                     Formatter)
from sys import (stdout, argv)
from argparse import (ArgumentParser, FileType)

# File constants
FNEXIST = 1
FNOPEN = 2
FOPEN = 3
FLOCKERR = 4

# Log constants
LOG_CHECKIN = 1
LOG_CHECKOUT = LOG_CHECKIN << 1

# Opt constants
OPT_CHECKIN = 2
OPT_CHECKOUT = OPT_CHECKIN << 1

def MapCmdLineOpts(opt=OPT_CHECKIN, kwCLO={}):
    """
    @Description
    Extract options passed to checkout or checkin utilities. kwCLO
    modified an attribute from current class that calls this function,
    this way an item can be transfered to Vitis server like port.
    
    Form-factor:
    --port=<port-number>, this can be left blank to get automatically the port
    --ip=<ip-address>, this can be left blank to use localhost
    --fastsave=<speed-level>, levels: 1, 2, 3, these influence nr. of threads,
                               if left blank no other thread is created
    --outputfile=<save-log-messages-into-a-file>, path to a file or name of file
    
    The above options order is not important, so they can be passed
    in any possible order.
    """
    lsEntries = ["--port", "--ip", "--fastsave", "--outputfile"]
    for itm in lsEntries:
        kwCLO[itm] = ""

    if len(argv) > 1:
        parser = ArgumentParser(
                    description="Checkin options" if opt == OPT_CHECKOUT else
                                "Checkout options")
        parser.add_argument(f"{lsEntries[0]}", type=int, help="Server's port number")
        parser.add_argument(f"{lsEntries[1]}", type=str, help="Server's ip or localhost")
        parser.add_argument(f"{lsEntries[2]}", type=int, help="Influences no. of threads")
        parser.add_argument(f"{lsEntries[3]}", type=FileType("w"),
                            help="Redirect output of used utility")
        args = parser.parse_args()
        for idx in range(0, len(lsEntries)):
            # Trim the first two `--` dashes.
            sTmp = lsEntries[idx][2:]
            attr = getattr(args, sTmp)
            if attr is not None:
                kwCLO[lsEntries[idx]] = attr

def LOG(msg="",
        format="%(asctime)s %(levelname)s : %(message)s",
        nameOtp=LOG_CHECKIN
        ):
    """
    @Description
    Custom logging mechanism for displaying informations during
    the execution of check in workflow.

    @Parameters
    msg: message to display to stdout
    format: default format: time - level name - actual message
    nameOpt: what utility uses this logger
    """
    name = "Info Checkin" if nameOtp == LOG_CHECKIN else "Info Checkout"
    
    class _LOG(Logger):
        """
        Simple layout class for logging
        """
        def __init__(self, msg, fmt, name="", stream=stdout):
            super().__init__(name=name, level=INFO)
            self.sHnd = StreamHandler(stream)
            self.message = msg
            self._formater = Formatter(fmt)
            self.sHnd.setFormatter(self._formater)
            self.addHandler(self.sHnd)
            self.log(level=INFO, msg=self.message)
    # Nested log
    _locLog = _LOG(msg, format, name)

def CkFileOpenBlock(filename : str,
                    osName=""
                    ) -> int:
    """
    @Description
    Options for file management on win are found at
    win32 -> fileio -> file-management-functions on win website. For lnx,
    on man-pages -> man0 -> fcnt.h. The `.lock` file is assumed to be in
    workspace root dir.
    """
    # Check if file exits.
    if not access(filename, F_OK):
        # File doesn't exist.
        return FNEXIST
    
    if osName == "Windows":
        # Refs
        # _sopen_s = cdll.msvcrt._sopen_s
        _close = cdll.msvcrt._close
        _locking = cdll.msvcrt._locking
        _filelength = cdll.msvcrt._filelength
        # _get_errno = cdll.msvcrt._get_errno
        # Win CRT in general returns 0 if any error did not occur.
        _FSC = 0
        _LK_UNLCK = 0x0
        # _SH_DENYRW, _SH_NOPEN = 0x10, 0x11
        # _O_RDONLY, _S_IREAD = 0x0, 0x0100
        _O_RDONLY = 0x0
        # _SH_DENYWR = 0x20
        # _sopen 'gives back' a descriptor, but only -1; fHnd
        # erRet = _sopen_s(fHnd.value, filename, _O_RDONLY)
        with open(filename, "r") as lcFile:
            fHnd = lcFile.fileno()
            isRd = lcFile.readable()
            if isRd:
                # ln = _filelength(fHnd)
                _locking(fHnd, _LK_UNLCK, 0)
                _close(fHnd)
                # File is not opened by anyone else.
                return FNOPEN
    elif osName == "Linux" or osName == "Darwin":
        # This module comes only on Unix like platforms.
        from fcntl import (lockf, LOCK_UN)
        # Get file descriptor.
        with open(filename, "r") as lcFile:
            lcFd = lcFile.fileno()
            # Unlock file directly.
            try:
                lockf(lcFd, LOCK_UN, SEEK_END)
                return FNOPEN
            except OSError as err:
                # Interpret error from lockf in some way.
                LOG(msg=f"{err.__cause__}" + f"{err.__context__}")
                return FLOCKERR
    else:
        LOG("This OS: " + osName + " is not supported!")
