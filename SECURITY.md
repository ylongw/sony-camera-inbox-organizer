# Security

## Reporting

Open a private GitHub security advisory for vulnerabilities. Do not include NAS
addresses, credentials, media files, or private metadata in public issues.

## Deployment

- Run the container as the UID/GID that owns the media directories.
- Keep `no-new-privileges` enabled and do not mount the Docker socket.
- Expose the Web port only to a trusted LAN or an authenticated reverse proxy.
- Mount one media root and grant only the directories required by the configured
  workflow.
- Treat `hooks.after_publish` as code execution. Configure only an executable
  controlled by the NAS administrator.

The project does not require a reference Live Photo and does not bundle private
photo metadata. External adapters and their credentials must remain outside the
repository and image.
