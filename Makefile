.PHONY: test image run

test:
	pytest

image:
	docker build -t sony-camera-inbox-organizer:local .

run:
	IMAGE=sony-camera-inbox-organizer:local docker compose up -d
