
from ctypes import cdll
from logging import (Logger, INFO, StreamHandler,
                     Formatter)
from sys import stdout

FNEXIST = 1
FNOPEN = 2
FOPEN = 3
FLOCKERR = 4

def LOG(msg="",
        format="%(asctime)s %(levelname)s : %(message)s",
        ):
    """
    @Description
    Custom logging mechanism for displaying informations during
    the execution of check in workflow.

    @Parameters
    msg: message to display to stdout
    format: default format: time - level name - actual message
    """
    class _LOG(Logger):
        """
        Simple layout class for logging
        """
        def __init__(self, msg, fmt, name="Info Checkin", stream=stdout):
            super().__init__(name=name, level=INFO)
            self.sHnd = StreamHandler(stream)
            self.message = msg
            self._formater = Formatter(fmt)
            self.sHnd.setFormatter(self._formater)
            self.addHandler(self.sHnd)
            self.log(level=INFO, msg=self.message)
    # Nested log
    _locLog = _LOG(msg, format)

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
        return UtilityWS.FNEXIST
    
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
                return UtilityWS.FNOPEN
    elif osName == "Linux" or osName == "Darwin":
        # This module comes only on Unix like platforms.
        from fcntl import (lockf, LOCK_UN)
        # Get file descriptor.
        with open(filename, "r") as lcFile:
            lcFd = lcFile.fileno()
            # Unlock file directly.
            try:
                lockf(lcFd, LOCK_UN, SEEK_END)
                return UtilityWS.FNOPEN
            except OSError as err:
                # Interpret error from lockf in some way.
                LOG(msg=f"{err.__cause__}" + f"{err.__context__}")
                return UtiltyWS.FLOCKERR
    else:
        LOG("This OS: " + osName + " is not supported!")
