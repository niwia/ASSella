"""Post-download finalisation for TaskManager.

Everything that happens after a depot download completes: writing the Steam ACF
manifest, seeding Steam's depotcache and the DDM delta cache, emitting
GreenLuma/EOS/browser-wrapper integration files, and registering the finished
game with SLSsteam.

This is a distinct phase of a job, so it lives here rather than inside the
orchestrator. Composed back into TaskManager via ``PostInstallMixin`` so the
public API is unchanged.
"""

from .post_install import PostInstallMixin

__all__ = ["PostInstallMixin"]