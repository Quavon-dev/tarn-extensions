# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: open this repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue, pull request or discussion for a
security problem.

This covers the catalog itself: its signing workflow, the signed index, and the packages mirrored
here. A problem in Tarn's handling of extensions belongs to
[tarn-browser](https://github.com/Quavon-dev/tarn-browser/security), whose
[security policy](https://github.com/Quavon-dev/tarn-browser/blob/main/SECURITY.md) and response
times apply here too.

## Malicious or compromised extensions

An extension in the catalog that turns out to be harmful is removed from `catalog.json` and its
package added to `revoked` (`id` and `sha256`); Tarn turns revoked packages off at its next catalog
check. Report such an extension the same way, through **Report a vulnerability**.
