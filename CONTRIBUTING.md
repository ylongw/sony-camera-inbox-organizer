# Contributing

1. Keep NAS-specific SDKs, credentials, and absolute personal paths out of the
   core repository.
2. Add tests for metadata box changes and organizer behavior.
3. Run `pytest` and build the Docker image before opening a pull request.
4. Use synthetic or explicitly redistributable fixtures only. Never commit a
   user's Live Photo as a compatibility template.

Container publishing is intentionally limited to a manual workflow dispatch or
a `v*` tag. Publishing to Docker Hub requires repository secrets named
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`; never place these values in files.
