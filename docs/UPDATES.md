# Signed Update And Rollback Policy

Beta updates must be signed with the same trusted publisher identity as the
installer. The update manifest, application, backend executables, and installer are
release artifacts and must be verified before distribution.

Mimir must preserve prior session folders across upgrade, failed upgrade, rollback,
and uninstall unless the user explicitly requests data removal. A failed update must
leave the previous signed build usable. The private beta does not fetch unsigned
packages or execute arbitrary updater commands.

No updater endpoint or public key is committed until release signing infrastructure
exists. The strict release checker therefore treats update/rollback VM evidence as a
blocker rather than shipping a placeholder trust configuration.
