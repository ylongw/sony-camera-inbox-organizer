# Contributing

1. Keep NAS-specific SDKs, credentials, and absolute personal paths out of the
   core repository.
2. Add tests for metadata box changes and organizer behavior.
3. Run `pytest` and build the Docker image before opening a pull request.
4. Use synthetic or explicitly redistributable fixtures only. Never commit a
   user's Live Photo as a compatibility template.
