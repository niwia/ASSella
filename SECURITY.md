# Security Policy

## Supported Versions

Security updates are prioritized for the latest official stable release and active preview channels.

| Version / Channel | Supported          | Status |
| ----------------- | ------------------ | ------ |
| **Stable (`v3.0.x`)** | :white_check_mark: | Fully supported with critical security patches |
| **Beta (`v3.1.x-beta`)** | :white_check_mark: | Supported (preview fixes applied in upcoming beta builds) |
| **Canary (`canary`)** | :warning: | Bleeding-edge development branch (fixes land on trunk) |
| **< 3.0.0** | :x: | Unsupported; please update to the latest release |

---

## Reporting a Vulnerability

The ASSella team takes security and privacy seriously. If you discover a vulnerability, please disclose it responsibly so we can fix it before public disclosure.

> [!IMPORTANT]
> **DO NOT** report security vulnerabilities via public GitHub Issues, Pull Requests, or public Discord channels.

### Preferred Method: GitHub Private Vulnerability Reporting

Please submit reports privately through GitHub's native advisory interface:

👉 **[Submit a Private Security Report](https://github.com/niwia/ASSella/security/advisories/new)**

### What to Include in Your Report

To help us triage and resolve the issue quickly, please provide:
1. **Summary**: A clear description of the vulnerability and its potential impact.
2. **Reproduction Steps**: Step-by-step instructions or a safe Proof of Concept (PoC).
3. **Affected Components**: Which files, modules, or release channels are impacted.
4. **Environment Details**: Operating system (SteamOS, Arch, Bazzite, NixOS, etc.) and ASSella version.

---

## What We Consider In Scope

- Credential, API token, or session leakage (e.g. Hubcap Manifest API keys, Steam tokens, unencrypted storage in `ACCELA.conf`).
- Remote code execution (RCE) or malicious payload injection via untrusted manifest data, API responses, or workshop items.
- Insecure path traversal or unauthorized file system deletion/overwrites.
- Tampering with system services or background companion bridges (`epsilon`, `uran`) outside intended sandboxes.

---

## Response Timeline & Coordination

- **Initial Response**: We will acknowledge receipt of your report within **48–72 hours**.
- **Investigation & Patching**: We will work with you to verify the vulnerability and prepare a patch in private.
- **Coordinated Disclosure**: Once a patch is released, a public security advisory will be published, and you will be credited for the discovery (unless you request anonymity).

Thank you for helping keep ASSella and its users safe!
