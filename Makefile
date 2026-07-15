.PHONY: test image run

test:
	pytest

image:
	docker build -t sony-camera-inbox-organizer:local .

run:
	docker compose up -d --build
