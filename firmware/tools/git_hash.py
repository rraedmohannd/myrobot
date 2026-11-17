# PlatformIO extra script: injects SCR_GIT_HASH (first 8 hex digits of the commit, as a number)
# for DIAG_REPORT (lib/app/scr_version.h). 0 when git or the repository is not available.
# The hash is the commit of HEAD; uncommitted changes are not visible in it.
import subprocess

Import("env")  # noqa: F821  (provided by PlatformIO/SCons)

value = 0
try:
    out = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "rev-parse", "--short=8", "HEAD"],
        cwd=env.subst("$PROJECT_DIR"),  # noqa: F821
        stderr=subprocess.DEVNULL,
    )
    value = int(out.decode().strip()[:8], 16)
except Exception:  # noqa: BLE001  no git, not a repository, ...
    value = 0

env.Append(CPPDEFINES=[("SCR_GIT_HASH", "0x%08Xu" % value)])  # noqa: F821
print("SCR_GIT_HASH = 0x%08X" % value)
