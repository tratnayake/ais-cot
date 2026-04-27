CONTAINER := aisstream-tak

.DEFAULT_GOAL := help

.PHONY: help build up down restart rebuild logs status shell

help:
	@echo "AIS → TAK Gateway"
	@echo ""
	@echo "Targets:"
	@echo "  build     Build the Docker image"
	@echo "  up        Start the gateway in the background"
	@echo "  down      Stop and remove the container"
	@echo "  restart   Stop, rebuild, and start"
	@echo "  rebuild   Full clean rebuild (no cache) and start"
	@echo "  logs      Tail live container logs"
	@echo "  status    Show container status"
	@echo "  shell     Open a shell inside the running container"

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

restart: down build up

rebuild:
	docker compose down
	docker compose build --no-cache
	docker compose up -d

logs:
	docker compose logs -f

status:
	docker compose ps

shell:
	docker exec -it $(CONTAINER) /bin/sh
