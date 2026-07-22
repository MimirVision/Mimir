# Signed Update And Rollback Policy

Beta updates must be signed with the same trusted publisher identity as the
installer. The update manifest, application, backend executables, and installer are
release artifacts and must be verified before distribution.

Mimir must preserve prior session folders across upgrade, failed upgrade, rollback,
and uninstall unless the user explicitly requests data removal. A failed update must
leave the previous signed build usable. The private beta does not fetch unsigned
packages or execute arbitrary updater commands.

The updater verification public key is pinned in the packaged application and Tauri
produces signed updater artifacts during the trusted release build. The HTTPS update
endpoint remains deliberately unset until the invite-only distribution host exists;
an internal build therefore cannot fetch updates accidentally. The strict release
checker continues to block invitations until a signed update and rollback are tested
on clean Windows 10 and Windows 11 machines with session preservation verified.
The release build injects the approved manifest URL through
`MIMIR_UPDATE_ENDPOINT`; development and unsigned internal builds contain no update
endpoint.
