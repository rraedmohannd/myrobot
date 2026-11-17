// Firmware identity, reported in DIAG_REPORT (interface spec A3). The ONE place of the version.
#pragma once

#define SCR_FW_VERSION_MAJOR 2
#define SCR_FW_VERSION_MINOR 0
#define SCR_FW_VERSION_PATCH 0

// First 8 hex digits of the git commit as a number, injected at build time by
// tools/git_hash.py (extra_script in platformio.ini). 0 = unknown (no git, dirty checkout
// outside a repository, ...).
#ifndef SCR_GIT_HASH
#define SCR_GIT_HASH 0
#endif
