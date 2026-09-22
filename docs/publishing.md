# Maintainer: publish to PyPI

The workflow is prepared but has not been run. The project remains installable from
GitHub and release wheels. Do not advertise a PyPI installation until publishing succeeds.

## One-time account setup

In your PyPI account, register a pending trusted publisher:

| Field | Value |
| --- | --- |
| Project name | retrace-engine |
| Owner | Sanjays2402 |
| Repository | retrace |
| Workflow | publish.yml |
| Environment | pypi |

See [PyPI's official setup instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
A pending publisher does not reserve the package name. Confirm name availability during setup.
No API token belongs in this repository.

Create the `pypi` GitHub environment and optionally require your approval there.
Once the release's CI and artifacts are verified, manually run **Publish to PyPI**,
selecting its exact version tag. The workflow refuses branch runs and checks tag/version
agreement, tests, builds, and installs the wheel before publishing with OIDC.

After success, test `python -m pip install retrace-engine==VERSION` in a fresh environment,
then update the installation guide. PyPI versions are immutable; fixes need a new version.
