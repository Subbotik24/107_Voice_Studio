# 0.4 Production Candidate — Signing and Key Custody Runbook

## Status and scope

This is a preparation runbook for a future 0.4 production candidate. It does
not create accounts, keys, signatures, notarization records, release
environments, tags, or published artifacts. Every external gate listed below
is `NOT_RUN` until a release owner records evidence from the real service or
device.

The build, validation, signing, and publication duties are separate. A build
produces an identified artifact and digest; a signing operator consumes only
that approved artifact; a second approver verifies the evidence before any
publication. The original artifact is retained for audit and rollback.

## Non-negotiable custody rules

- No private key, certificate export, access token, recovery code, or signing
  credential belongs in this repository, an artifact, CI logs, general CI
  variables, or developer settings.
- Use least-privilege identities and short-lived OIDC federation where the
  provider supports it. Long-lived secrets must not be used as a convenience
  fallback without an approved exception and an expiry.
- Two people approve every production signing and publication: the release
  operator performs the controlled action and an independent approver checks
  the source, artifact digest, signature/notarization evidence, and scope.
- The key inventory is authoritative for purpose, algorithm, owner, custodian,
  creation date, expiry date, storage boundary, public fingerprint, rotation
  date, and revocation contact. Inventory entries contain metadata only, never
  private material.
- A release record retains source revision, artifact names and SHA-256 digests,
  tool versions, signer identity, approval identities, timestamp evidence, and
  verification results. It must not retain private paths or secret material.

## Separate custody domains

### Apple Developer ID Application and notarization

The Developer ID Application certificate and its private key are held only in
the approved macOS signing host's protected keychain or an approved hardware
custody service. The notarization credential is held in the same dedicated
signing boundary or provided through a short-lived least-privilege mechanism;
it is never copied into the repository, a release asset, general CI, or a
developer workstation profile.

The signing operator verifies the exact artifact digest before signing. The
approver independently verifies the code-signing result, notarization ticket
or service response, staple/assessment result where applicable, and the
recorded timestamp. Failed notarization is a release stop, not a reason to
replace the artifact silently.

### Azure Trusted Signing identity

The Azure Trusted Signing account and certificate profile are a separate
custody domain from Apple and Ed25519. The signing identity is service-managed;
its private key is not exported. Access is through a dedicated Azure identity
with only the required signing role, preferably using OIDC federation and
short-lived credentials. No Azure access token or identity secret is stored in
workflow YAML, repository variables, logs, artifacts, or developer settings.

The operator records the profile/account metadata, artifact digest, signing
result, certificate chain evidence, and trusted timestamp. The approver checks
that the identity is the intended production identity and that the verification
was performed on the exact retained artifact.

### Offline Ed25519 update-manifest private key

The update-manifest Ed25519 private key is kept offline in the separately
approved encrypted key custody boundary. It is not present on the build host,
general CI, a developer machine, an artifact, or a release log. The online
manifest contains only the public verification key and signed metadata after a
separate product/security approval; the private key never crosses the offline
boundary merely to simplify automation.

The operator transfers the approved manifest input through the controlled
offline procedure, signs it, verifies the signature with the public key, and
records only the public fingerprint and verification evidence. The approver
checks that the manifest digests match the retained artifacts and that no
unsigned or unexpected update entry was introduced.

## Inventory, expiry, rotation, and revocation

The release owner maintains one inventory entry for each Apple signing
credential, Azure identity/profile, and Ed25519 key. The entry names the
purpose and owning team, responsible custodian, provider/account boundary,
public fingerprint or certificate identifier, created/expiry dates, next
rotation date, backup/recovery custody, and revocation contact. It does not
contain a private key, token, seed, password, or export.

Before expiry, the owner schedules a replacement, validates the new public
identity against a test artifact, obtains two-person approval, and records the
overlap and retirement dates. After cutover, the old identity is disabled or
revoked and verification confirms that the release process no longer accepts
it. Emergency revocation follows the same evidence rules but is initiated
immediately when compromise is suspected.

If a key or identity may be compromised: stop signing and publication; preserve
the relevant logs and artifact digests without copying secret material; notify
the owner and security contact; revoke or disable the identity; rotate affected
credentials; assess signed artifacts and update metadata; publish a bounded
customer/security notice if required; and document the incident and recovery
approval. Do not overwrite evidence or quietly re-sign the same artifact.

## Release evidence and recovery drill

For each candidate, the release record must link the approved source revision
to the exact artifact SHA-256, signature verification output, certificate or
identity evidence, trusted timestamp, and Apple notarization evidence where
applicable. The verification is performed from a clean validation environment
and includes the intended macOS and Windows installation/launch checks when
those devices are available.

Before production approval, the owners rehearse recovery with non-production
credentials or a documented provider sandbox: retrieve the inventory and
public verification material, restore the controlled signing boundary, rotate a
test identity, revoke it, verify rejection of the revoked identity, and
produce a replacement verification record. The drill must confirm that no
private material is required from repository history or CI logs. Record the
date, participants, scenario, result, and follow-up; do not record secrets.

## External gate checklist (`NOT_RUN` until evidenced)

- `NOT_RUN` — Apple Developer Program account, Developer ID Application
  certificate, and notarization access approved and owned.
- `NOT_RUN` — Azure Trusted Signing account, certificate profile, identity
  permissions, and OIDC federation approved.
- `NOT_RUN` — offline Ed25519 key generated, inventoried, backed up, and
  independently recovery-tested.
- `NOT_RUN` — protected GitHub release environment configured with required
  reviewers and no broad secret exposure.
- `NOT_RUN` — branch protection requires the approved checks and two-person
  release review.
- `NOT_RUN` — physical macOS and Windows target devices available for clean
  install, signature, notarization, and rollback checks.
- `NOT_RUN` — legal, licensing, trademark, and distribution approval for the
  0.4 product name, installer, update channel, and included assets.
- `NOT_RUN` — production artifact signing, notarization, trusted timestamps,
  publication, and rollback drill.

Until every applicable item has independently recorded evidence, the 0.4
candidate remains unsigned/unpublished preparation and must not be described
as a completed production release.
