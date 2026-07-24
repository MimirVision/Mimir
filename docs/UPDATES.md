# Signed Update And Rollback Policy

Beta updates must be signed with the same trusted publisher identity as the
installer. The update manifest, application, backend executables, and installer are
release artifacts and must be verified before distribution.

Mimir must preserve prior session folders across upgrade, failed upgrade, rollback,
and uninstall unless the user explicitly requests data removal. A failed update must
leave the previous signed build usable. The beta does not fetch unsigned
packages or execute arbitrary updater commands.

The updater verification public key is pinned in the packaged application and Tauri
produces signed updater artifacts during the trusted release build. The HTTPS update
endpoint remains deliberately unset until a trusted distribution host exists; an
internal build therefore cannot fetch updates accidentally. This holds regardless of
whether the installer itself is currently signed -- auto-updates are a separate,
higher-risk surface and stay off until they can be fully verified. The strict release
checker continues to block distribution until a signed update and rollback are tested
on clean Windows 10 and Windows 11 machines with session preservation verified.
The release build injects the approved manifest URL through
`MIMIR_UPDATE_ENDPOINT`; development and unsigned internal builds contain no update
endpoint.
